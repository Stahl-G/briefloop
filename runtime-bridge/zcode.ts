import {readFileSync} from 'node:fs';
import {homedir} from 'node:os';
import path from 'node:path';
import {createJsonLineStream} from '../third_party/open-design/core/json-line-stream.js';

// Protocol: `zcode --prompt ... --output-format stream-json` writes one session
// event per line. ZCode retired its ACP surface for its own ZCode Protocol, so
// the app-server/agent-server framing is not reachable through this bridge.
const MODES = ['build', 'edit', 'plan', 'yolo'];
const IMAGE_TYPES = ['.png', '.jpg', '.jpeg', '.webp'];
// The prompt travels in argv: ZCode headless has no stdin prompt channel, so a
// long prompt has to fail here rather than as a spawn-level argument overflow.
const PROMPT_LIMIT = 200000;
const THINK = /^(think|thinking|reasoning)$/i;

function configuredModel() {
 try {
  const file = JSON.parse(readFileSync(path.join(homedir(), '.zcode', 'cli', 'config.json'), 'utf8'));
  const main = file?.model?.main;
  return typeof main === 'string' ? main.trim() : '';
 } catch {return ''}
}

export function zcodeModels() {
 const main = configuredModel();
 return {models:[{id:'default',label:main?'配置的模型：'+main:'宿主默认模型'}],source:'host_default_only',
  note:'ZCode 的无界面运行没有选择模型的参数，本轮使用 ~/.zcode/cli/config.json 里 model.main 配置的模型；要换模型请在 ZCode 中设置。'};
}

function toolOutput(payload:any) {
 const result = payload.result;
 if (payload.kind === 'error') return payload.error?.message || payload.error?.type || '工具失败';
 if (result && typeof result === 'object' && 'content' in result) return result.content;
 return result;
}

export async function runZcode(p:any, state:any, launch:any, terminate:any, emit:any) {
 if (p.model && p.model !== 'default') throw Error('ZCode 无界面运行不接受模型参数：只能使用它自己配置的模型，请在 ZCode 中切换后重试');
 if (p.prompt.length > PROMPT_LIMIT) throw Error('提示词超出 ZCode 命令行可传长度');
 const args = ['--prompt', p.prompt, '--cwd', p.cwd, '--output-format', 'stream-json', '--no-color'];
 // ZCode --prompt defaults to yolo, regardless of the interactive setting.
 // Preserve legacy 'native' records as the ordinary build mode; yolo requires
 // an explicit choice in BriefLoop and is never inferred from a missing flag.
 const selected = p.host_options?.mode;
 const mode = !selected || selected === 'native' ? 'build' : selected;
 if (!MODES.includes(mode)) throw Error('Invalid ZCode permission mode');
 args.push('--mode', mode);
 if (p.session_id) args.push('--resume', p.session_id);
 for (const image of p.images || []) {
  const file = path.resolve(typeof image === 'string' ? image : image.path);
  if (!IMAGE_TYPES.includes(path.extname(file).toLowerCase())) throw Error('不支持的图片格式');
  if (readFileSync(file).length > 20 * 1024 * 1024) throw Error('图片超过 20 MiB');
  args.push('--attach', file);
 }
 const child = launch(state.bin, args, p.cwd);
 state.child = child; state.cancel = () => terminate(child);
 // tool.updated carries the call id but not the name; model.streaming named it.
 const names = new Map<string, string>();
 let lastSession:string|null = null, textSeen = false, completed = false, response = '', failure = '', stderr = '';
 await new Promise<void>((resolve, reject) => {
  const timer = p.timeout_ms ? setTimeout(() => {terminate(child); reject(Error('ZCode turn timed out'));}, p.timeout_ms) : null;
  const parser = createJsonLineStream((m:any) => {
   if (m.sessionId && m.sessionId !== lastSession) {lastSession = m.sessionId; emit(p.execution_id, 'session', {session_id:m.sessionId});}
   const payload = m.payload || {};
   if (m.type === 'model.streaming') {
    if (payload.kind === 'text_delta' && payload.delta) {textSeen = true; emit(p.execution_id, 'text', {text:payload.delta, delta:true});}
    else if (payload.kind === 'reasoning_delta' && payload.delta) emit(p.execution_id, 'reasoning', {text:payload.delta, delta:true});
    else if (payload.kind === 'tool_call' && payload.toolCallId && !THINK.test(payload.toolName || '')) {
     names.set(payload.toolCallId, payload.toolName);
     emit(p.execution_id, 'tool', {id:payload.toolCallId, name:payload.toolName || '工具操作', status:'running', input:payload.input});
    }
   } else if (m.type === 'tool.updated' && payload.toolCallId) {
    const name = names.get(payload.toolCallId) || payload.toolName;
    if (THINK.test(name || '')) return;
    if (payload.kind === 'started') emit(p.execution_id, 'tool', {id:payload.toolCallId, name:name || '工具操作', status:'running'});
    else if (payload.kind === 'result' || payload.kind === 'error') {
     const ok = payload.kind === 'result' && payload.result?.success !== false;
     emit(p.execution_id, 'tool', {id:payload.toolCallId, name:name || '工具操作', status:ok ? 'completed' : 'failed', output:toolOutput(payload)});
    }
   } else if (m.type === 'turn.completed') {
    completed = true;
    if (payload.usage) emit(p.execution_id, 'usage', {usage:payload.usage});
    if (typeof payload.response === 'string') response = payload.response;
    if (payload.resultType === 'cancelled') state.cancelled = true;
    else if (payload.resultType && payload.resultType !== 'success') failure = 'ZCode 未正常结束：' + payload.resultType;
   } else if (m.type === 'turn.failed') {
    failure = String(payload.error?.message || payload.error?.type || 'ZCode 回合失败').slice(0, 2000);
   }
  });
  child.stdout.setEncoding('utf8'); child.stdout.on('data', c => parser.feed(c));
  child.stderr.setEncoding('utf8'); child.stderr.on('data', c => {stderr = (stderr + c).slice(-8192);});
  child.stdin.on('error', () => {});
  child.on('error', e => {clearTimeout(timer); reject(e);});
  child.on('close', code => {
   clearTimeout(timer); parser.flush();
   if (state.cancelled) return resolve();
   if (failure) return reject(Error(failure));
   if (!completed) return reject(Error('ZCode 未完成本回合（exit ' + code + '）' + (stderr.trim() ? '：' + stderr.trim().slice(-400) : '')));
   if (!textSeen && response.trim()) {textSeen = true; emit(p.execution_id, 'text', {text:response, delta:true});}
   // plan mode ends the turn on ExitPlanMode, which headless cannot approve, so
   // the turn completes with no answer at all. Say that instead of "completed".
   if (!textSeen) return reject(Error('ZCode 结束时没有给出回答：规划模式下它只提交计划而无法在无界面下继续，请改用 build 模式或在提示中要求直接给出结论'));
   if (code !== 0) return reject(Error('ZCode exited with code ' + code));
   resolve();
  });
  child.stdin.end();
 });
}
