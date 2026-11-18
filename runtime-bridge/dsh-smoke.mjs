// Opt-in real DSH ACP check with a loopback-only synthetic model, never personal credentials.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, mkdir, rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createInterface} from 'node:readline';

const bin = process.env.DSH_TEST_BIN;
if (!bin || !path.isAbsolute(bin)) throw Error('Set DSH_TEST_BIN to an absolute DSH executable (Node >=24.9 for DSH 0.2.2).');
const root = await mkdtemp(path.join(tmpdir(), 'briefloop-dsh-'));
const cwd = path.join(root, 'workspace');
await mkdir(cwd);
const requests = [];
const server = createServer((req, res) => {
 let body = '';
 req.on('data', chunk => { body += chunk; });
 req.on('end', () => {
  if (req.method !== 'POST' || !req.url.endsWith('/v1/messages')) return res.writeHead(404).end();
  const payload = JSON.parse(body);
  requests.push(payload);
  const events = [
   {type:'message_start',message:{id:'synthetic',type:'message',role:'assistant',model:'fixture',content:[],usage:{input_tokens:1,output_tokens:0}}},
   {type:'content_block_start',index:0,content_block:{type:'text',text:''}},
   {type:'content_block_delta',index:0,delta:{type:'text_delta',text:'SYNTHETIC_DSH_OK'}},
   {type:'content_block_stop',index:0},
   {type:'message_delta',delta:{stop_reason:'end_turn',stop_sequence:null},usage:{output_tokens:1}},
   {type:'message_stop'}
  ];
  res.writeHead(200, {'content-type':'text/event-stream'});
  for (const event of events) res.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
  res.end();
 });
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const env = {
 PATH:process.env.PATH, HOME:root, USERPROFILE:root, TMPDIR:root,
 DSH_HOME:path.join(root, 'dsh'), DSH_AGENTS_HOME:path.join(root, 'agents'), DSH_TELEMETRY_DISABLED:'1',
 DEEPSEEK_API_KEY:'synthetic-local-only', DEEPSEEK_BASE_URL:`http://127.0.0.1:${server.address().port}`,
};
const child = spawn(process.execPath, ['src/briefloop/static/runtime-bridge.mjs'], {env, stdio:['pipe','pipe','pipe']});
const closed = new Promise(resolve => child.once('close', resolve));
let stderr = '';
child.stderr.on('data', chunk => { stderr += chunk; });
const frames = [];
createInterface({input:child.stdout}).on('line', line => frames.push(JSON.parse(line)));
let id = 0;
const wait = async predicate => {
 const until = Date.now() + 60000;
 while (Date.now() < until) {
  const frame = frames.find(predicate);
  if (frame) return frame;
  if (child.exitCode !== null) throw Error(`Bridge exited: ${stderr}`);
  await new Promise(resolve => setTimeout(resolve, 50));
 }
 throw Error(`Timeout: ${JSON.stringify(frames)} ${stderr}`);
};
const call = async (method, params) => {
 const requestId = ++id;
 child.stdin.write(JSON.stringify({id:requestId, method, params})+'\n');
 const frame = await wait(f => f.id === requestId);
 if (frame.error) throw Error(frame.error.message);
 return frame.result;
};
try {
 const params = {runtime_id:'deepseek-harness', path:bin, cwd};
 const catalog = await call('list_models', params);
 assert.ok(catalog.models.some(m => m.id === 'deepseek-official/deepseek-flash'));
 let session;
 for (const execution_id of ['first', 'resume']) {
  await call('start', {...params, execution_id, session_id:session, model:'deepseek-official/deepseek-flash',
   prompt:`SYNTHETIC_${execution_id}`, permission:'runtime-native', allow_web:null, timeout_ms:45000});
  const end = await wait(f => f.params?.execution_id === execution_id && f.params?.kind === 'end');
  assert.equal(end.params.status, 'completed', JSON.stringify(frames));
  const turn = frames.filter(f => f.params?.execution_id === execution_id).map(f => f.params);
  assert.ok(turn.some(f => f.kind === 'text' && f.text.includes('SYNTHETIC_DSH_OK')));
  const current = turn.find(f => f.kind === 'session').session_id;
  if (session) assert.equal(current, session);
  session = current;
 }
 assert.equal(requests.length, 2);
 assert.ok(JSON.stringify(requests[1].messages).includes('SYNTHETIC_first'));
 console.log('PASS: real DSH ACP catalog, selected model, prompt and persisted session resume; synthetic loopback model only.');
} finally {
 child.stdin.end();
 const timeout = setTimeout(() => child.kill(), 5000);
 await closed;
 clearTimeout(timeout);
 await new Promise(resolve => server.close(resolve));
 await rm(root, {recursive:true, force:true});
}
