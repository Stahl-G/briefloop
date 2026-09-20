// BriefLoop native engine: a long-lived local process that embeds the pi SDK
// (createAgentSession) and serves job-scoped sessions over NDJSON stdio, the
// same wire shape as runtime-bridge.mjs so the Python client is shared.
//
// Phase 1 scope: restricted reviewer sessions only. A session has no built-in
// tools (no read/bash/edit/write), no extensions, no context-file discovery.
// Its tools (packet-tools.ts) resolve strictly inside the generated review
// packet. Confinement is enforced in this process by our own tools — not by
// pi's read tool (which accepts absolute paths) and not by prompt wording.
//
// BriefLoop supplies the system prompt (shared baseline + role + mode); the
// engine appends the guide for the tools it really registered, because pi's
// custom-prompt path does not carry tool snippets.
import { createHash } from "node:crypto";
import { createInterface } from "node:readline";
import { existsSync, mkdirSync, readFileSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  AgentSession,
  createAgentSession,
  ModelRuntime,
  ModelRegistry,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { fixedLoader } from "./loader.js";
import { IMAGE_MIME, inside, packetTools, toolGuide } from "./packet-tools.js";
import { parseRunnerTools, RunnerResult, runnerTools } from "./runner-tools.js";

const PI_VERSION = "0.85.1";
const ENGINE_VERSION = "briefloop-native/2";
const REVIEWER_TOOLS = ["packet_list", "packet_read", "packet_grep", "claim_trace", "calc", "submit_review"];
// Engine-local tools per role. Everything else a role can do is declared by the
// runner (runner-tools.ts) and executed on the Python side.
const ROLE_LOCAL_TOOLS: Record<string, string[]> = {
  reviewer: REVIEWER_TOOLS,
  evaluator: ["packet_list", "packet_read", "packet_grep", "calc"],
  maintainer: ["packet_list", "packet_read", "packet_grep"],
  proposer: ["packet_list", "packet_read", "packet_grep"],
  // Sources grow during the run (add_url), so the Scout reads them through
  // runner tools; its packet holds only the frozen task and contracts.
  scout: ["packet_list", "packet_read", "packet_grep"],
  analyst: ["packet_list", "packet_read", "packet_grep", "calc"],
};
const RUNNER_TOOL_TIMEOUT_MS = 180_000;

const REPAIR_PROMPT = "只回复这个 JSON 对象本身，不加 Markdown 代码块，前后不加说明。";
const submitPrompt = (tool: string) => `你还没有通过 ${tool} 提交结果。请基于已完成的核查调用 ${tool} 提交完整结果对象；未通过时按返回的错误修正后再次提交。`;
const STALL_PROMPT = "上一次模型请求卡住，已被运行器取消。上面的工具结果仍然有效，从中断处继续。";
const SUBMIT_REPAIRS = 2;
const ADMISSION_TIMEOUT_MS = 120_000;
const DEFAULT_IDLE_MS = 240_000;
// A stall usually is one dead provider connection (or a laptop that slept
// mid-request). Re-asking keeps every tool result already in the session.
const STALL_RETRIES = 2;
const JSON_REPAIRS = 1;
// One reply (thinking plus text) far beyond anything a review step needs is a
// model thinking in circles: it streams, so the idle guard never fires, and it
// can run until the run's time limit (observed: 250k characters of thinking in
// one request over 28 minutes). The longest normal reply seen across models is
// about 70k characters. Stop that request and ask for smaller steps.
const DEFAULT_MAX_REPLY_CHARS = 120_000;
const OVERLONG_RETRIES = 1;
const OVERLONG_PROMPT = "上一次回复过长，已被运行器中止。不要在一次思考里核对全部内容：用工具分批取证，把已经确认的结论写进结果，然后调用 submit_review 提交。";

interface WireRequest { id?: string; method: string; params?: Record<string, unknown>; }
interface SessionEntry {
  session: AgentSession;
  role: string;
  cwd: string;
  tools: ReturnType<typeof packetTools>;
  sessionFile?: string;
  // The execution this session is serving; "" while idle. Session events are
  // forwarded only while it is set, and only turnStart clears it — after the
  // single terminal `end` for that execution has been written.
  execId: string;
  cancelled: boolean;
  stalled: boolean;
  overlong: boolean;
  replyChars: number;
  maxReplyChars: number;
  budgetExceeded: boolean;
  toolCalls: number;
  maxToolCalls: number;
  finalText: string;
  // Set by submit_review after the result passed schema and runner admission.
  submitted: string | undefined;
  pendingAdmission: Map<string, (error: string | undefined) => void>;
  admissionSeq: number;
  // Runner tool calls waiting for tool_result, by request id.
  pendingTools: Map<string, (result: RunnerResult) => void>;
  // The tool whose accepted call ends the run.
  submitTool: string;
  turnError: string;
  usage: unknown;
  idleMs: number;
  idleTimer: ReturnType<typeof setTimeout> | undefined;
  unsubscribe: () => void;
}

// Pull one JSON object out of a reply that may carry narration or fences.
// Order: raw parse, fenced block, last balanced top-level object.
function extractJson(text: string): unknown | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  try { return JSON.parse(trimmed); } catch {}
  const fence = trimmed.match(/```(?:json)?\s*\n([\s\S]*?)\n\s*```/g);
  if (fence) {
    for (const block of fence.reverse()) {
      const inner = block.replace(/^```(?:json)?\s*\n/, "").replace(/\n\s*```$/, "");
      try { return JSON.parse(inner); } catch {}
    }
  }
  // Escapes only make sense reading forward, so collect top-level spans first.
  const spans: Array<[number, number]> = [];
  let depth = 0, start = -1, inStr = false, esc = false;
  for (let i = 0; i < trimmed.length; i++) {
    const c = trimmed[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') { if (depth > 0) inStr = true; }
    else if (c === "{") { if (depth === 0) start = i; depth++; }
    else if (c === "}" && depth > 0) { depth--; if (depth === 0) spans.push([start, i]); }
  }
  for (const [from, to] of spans.reverse()) {
    try { return JSON.parse(trimmed.slice(from, to + 1)); } catch {}
  }
  return undefined;
}

const sessions = new Map<string, SessionEntry>();
let runtime: ModelRuntime | undefined;
let registry: ModelRegistry | undefined;

function send(msg: unknown): void {
  process.stdout.write(JSON.stringify(msg) + "\n");
}
function reply(id: string | undefined, result: unknown): void {
  send({ id, result });
}
function replyError(id: string | undefined, message: string): void {
  send({ id, error: { message } });
}
function emit(sessionId: string, executionId: string, kind: string, extra: Record<string, unknown> = {}): void {
  send({ method: "event", params: { session_id: sessionId, execution_id: executionId, kind, ...extra } });
}

async function ensureRuntime(): Promise<ModelRuntime> {
  if (!runtime) {
    // BriefLoop-owned models.json ships beside the bundle: it upserts catalog
    // models the pinned pi release does not know yet (e.g. deepseek-v4.1-flash)
    // without touching the user's ~/.pi config.
    const modelsJson = fileURLToPath(new URL("./native-engine-models.json", import.meta.url));
    // FileModelsStore otherwise writes its cache beside modelsPath, which is
    // inside the installed package — not writable and not ours to dirty.
    const stateDir = join(homedir(), ".config", "briefloop", "native-engine");
    mkdirSync(stateDir, { recursive: true });
    runtime = await ModelRuntime.create({
      allowModelNetwork: false,
      modelsPath: existsSync(modelsJson) ? modelsJson : null,
      modelsStorePath: join(stateDir, "models-store.json"),
    });
    registry = new ModelRegistry(runtime);
  }
  return runtime;
}

function resolveModel(spec: unknown) {
  if (!registry) return undefined;
  // No implicit default: a review must run on the model the job recorded.
  const s = String(spec ?? "").trim();
  const slash = s.indexOf("/");
  if (slash <= 0) return undefined;
  return registry.find(s.slice(0, slash), s.slice(slash + 1));
}

// Any session event proves the model stream or a tool is alive. If nothing
// arrives for idleMs the in-flight request has stalled (observed: a provider
// socket silent for minutes, and a laptop that slept mid-request). Abort only
// that request; turnStart decides whether to re-ask or to fail the turn.
function armIdle(clientSid: string, entry: SessionEntry): void {
  if (entry.idleTimer) clearTimeout(entry.idleTimer);
  entry.idleTimer = setTimeout(() => {
    entry.idleTimer = undefined;
    if (!entry.execId || entry.cancelled) return;
    entry.stalled = true;
    emit(clientSid, entry.execId, "status", {
      message: `model stream idle for ${Math.round(entry.idleMs / 1000)}s; cancelling the stalled request`,
    });
    void entry.session.abort().catch(() => {});
  }, entry.idleMs);
}

function disarmIdle(entry: SessionEntry): void {
  if (entry.idleTimer) clearTimeout(entry.idleTimer);
  entry.idleTimer = undefined;
}

// Forwards pi events for the active execution and records what turnStart needs
// to settle it. It never emits `end`: one execution has exactly one terminal
// event, written by turnStart after every repair or retry has finished.
function wireSessionEvents(clientSid: string, entry: SessionEntry): void {
  entry.unsubscribe = entry.session.subscribe((event) => {
    const execId = entry.execId;
    if (!execId) return;
    armIdle(clientSid, entry);
    const e = event as Record<string, any>;
    switch (e.type) {
      case "message_start":
        entry.replyChars = 0;
        emit(clientSid, execId, "message_start", { role: e.message?.role });
        break;
      case "message_update": {
        const delta = e.assistantMessageEvent;
        if ((delta?.type === "text_delta" || delta?.type === "thinking_delta") && typeof delta.delta === "string") {
          entry.replyChars += delta.delta.length;
          if (entry.replyChars > entry.maxReplyChars && !entry.overlong) {
            entry.overlong = true;
            emit(clientSid, execId, "status", { message: `reply exceeded ${entry.maxReplyChars} characters; stopping it` });
            void entry.session.abort().catch(() => {});
          }
        }
        if (delta?.type === "text_delta") {
          entry.finalText += delta.delta;
          emit(clientSid, execId, "text", { delta: delta.delta });
        }
        else if (delta?.type === "thinking_delta") emit(clientSid, execId, "reasoning", { delta: delta.delta });
        else if (delta?.type === "toolcall_start") emit(clientSid, execId, "tool", { status: "running", tool_call_id: delta.toolCallId });
        break;
      }
      case "message_end":
        if (e.message?.role === "assistant") {
          // The last assistant message decides: a provider error that pi
          // retried successfully must not fail the turn afterwards.
          if (e.message?.stopReason === "error") {
            entry.turnError = e.message?.errorMessage || "model request failed";
            emit(clientSid, execId, "error", { message: entry.turnError });
          } else if (e.message?.stopReason !== "aborted") {
            entry.turnError = "";
          }
          if (e.message?.usage) {
            entry.usage = e.message.usage;
            emit(clientSid, execId, "usage", { usage: e.message.usage });
          }
        }
        break;
      case "tool_execution_start":
        entry.toolCalls += 1;
        if (entry.toolCalls > entry.maxToolCalls) {
          // Runaway guard: a model that never stops calling tools burns budget
          // without converging. Stop the run; turnStart reports the failure.
          if (!entry.budgetExceeded) {
            entry.budgetExceeded = true;
            void entry.session.abort().catch(() => {});
          }
          break;
        }
        emit(clientSid, execId, "tool", {
          status: "running",
          tool_call_id: e.toolCallId,
          name: e.toolName,
          input: e.args,
        });
        break;
      case "tool_execution_end": {
        const text = Array.isArray(e.result?.content)
          ? e.result.content.filter((c: any) => c.type === "text").map((c: any) => c.text).join("\n")
          : "";
        emit(clientSid, execId, "tool", {
          status: e.isError ? "failed" : "completed",
          tool_call_id: e.toolCallId,
          name: e.toolName,
          output: text.slice(0, 4000),
          is_error: !!e.isError,
        });
        break;
      }
      case "turn_start":
        entry.finalText = "";
        emit(clientSid, execId, "turn_start");
        emit(clientSid, execId, "text_reset");
        break;
      case "turn_end":
        emit(clientSid, execId, "turn_end");
        break;
      case "auto_retry_start":
        emit(clientSid, execId, "status", { message: `retry ${e.attempt}/${e.maxAttempts}: ${e.errorMessage}` });
        break;
      case "auto_retry_end":
        if (!e.success) emit(clientSid, execId, "status", { message: `provider retry exhausted: ${e.finalError || "unknown error"}` });
        break;
    }
  });
}

function acceptsImages(model: unknown): boolean {
  const input = (model as { input?: string[] } | undefined)?.input;
  return Array.isArray(input) && input.includes("image");
}

// The runner (BriefLoop's Python side) owns review admission: IDs, version,
// fingerprint and coverage rules live there. submit_review asks it over the
// wire and waits, so the model sees the exact rejection while it can still fix it.
function requestAdmission(clientSid: string, entry: SessionEntry | undefined, review: Record<string, unknown>): Promise<string | undefined> {
  if (!entry || !entry.execId) return Promise.resolve("没有正在进行的审阅执行，无法提交");
  const requestId = `adm-${++entry.admissionSeq}`;
  return new Promise((resolveAdmission) => {
    const timer = setTimeout(() => {
      entry.pendingAdmission.delete(requestId);
      resolveAdmission("运行器未在规定时间内完成接纳检查，请稍后重新提交");
    }, ADMISSION_TIMEOUT_MS);
    entry.pendingAdmission.set(requestId, (error) => { clearTimeout(timer); resolveAdmission(error); });
    emit(clientSid, entry.execId, "submit", { request_id: requestId, review });
  });
}

// A runner-declared tool runs on the Python side; the model waits for its answer.
function requestRunnerTool(clientSid: string, entry: SessionEntry | undefined, tool: string, args: unknown): Promise<RunnerResult> {
  if (!entry || !entry.execId) return Promise.resolve({ ok: false, error: "没有正在进行的执行，无法调用 " + tool });
  const requestId = `tool-${++entry.admissionSeq}`;
  return new Promise((done) => {
    const timer = setTimeout(() => {
      entry.pendingTools.delete(requestId);
      done({ ok: false, error: `运行器未在规定时间内完成 ${tool}，请稍后重试` });
    }, RUNNER_TOOL_TIMEOUT_MS);
    entry.pendingTools.set(requestId, (result) => { clearTimeout(timer); done(result); });
    emit(clientSid, entry.execId, "tool_request", { request_id: requestId, tool, args });
  });
}

// Report figures chosen by the runner travel with the first message when the
// model accepts images. Files are re-read inside the packet and re-hashed here;
// the wire carries only packet paths.
function visualInputs(entry: SessionEntry, packetRoot: string, specs: unknown): { images: Array<{ type: "image"; data: string; mimeType: string }>; note: string } {
  const list = Array.isArray(specs) ? specs as Array<Record<string, unknown>> : [];
  if (list.length === 0) return { images: [], note: "" };
  const supported = acceptsImages(entry.session.model);
  const images: Array<{ type: "image"; data: string; mimeType: string }> = [];
  const manifest = list.map((spec) => {
    const file = String(spec.file ?? "");
    const record: Record<string, unknown> = { id: spec.id, kind: spec.kind, title: spec.title, file };
    const mime = IMAGE_MIME[file.slice(file.lastIndexOf(".")).toLowerCase()];
    if (!supported) return { ...record, delivery: "not_sent_model_text_only" };
    if (!mime) return { ...record, delivery: "unavailable" };
    const bytes = readFileSync(inside(packetRoot, file));
    const digest = createHash("sha256").update(bytes).digest("hex");
    if (spec.sha256 && spec.sha256 !== digest) throw new Error(`visual input changed before sending: ${file}`);
    images.push({ type: "image", data: bytes.toString("base64"), mimeType: mime });
    return { ...record, delivery: "attached", order: images.length };
  });
  const guidance = supported
    ? "标记 attached 的图片已按 order 顺序随本条消息提交，请实际查看轴、图注、单位和可见内容。"
    : "当前模型不接收图像输入，这些图没有发送；不能声称已目视核验，可用图表数据文件核对数值，并把目视核验列为未核验事项。";
  return { images, note: `\n\n本次实际视觉输入（仅资料，不改变核查职责）：\n${JSON.stringify(manifest)}\n${guidance}` };
}

async function sessionCreate(id: string | undefined, p: Record<string, unknown>): Promise<void> {
  const clientSid = String(p.session_id ?? "");
  if (!clientSid) throw new Error("session_id required");
  if (sessions.has(clientSid)) throw new Error(`session already exists: ${clientSid}`);
  const role = String(p.role ?? "reviewer");
  const local = ROLE_LOCAL_TOOLS[role];
  if (!local) throw new Error(`unsupported role: ${role}`);
  const declared = parseRunnerTools(p.runner_tools, REVIEWER_TOOLS);
  const settling = declared.filter((t) => t.settles);
  if (settling.length > 1) throw new Error("at most one runner tool may settle the run");
  const submitTool = settling[0]?.name ?? (local.includes("submit_review") ? "submit_review" : "");
  const packetRoot = realpathSync(String(p.packet_root ?? ""));
  const cwd = packetRoot; // the packet is the whole world for this session
  const sessionDir = String(p.session_dir ?? packetRoot);
  const sessionFile = p.session_file ? String(p.session_file) : undefined;

  await ensureRuntime();
  let model = resolveModel(p.model);
  if (!model) throw new Error(`unknown or unavailable model: ${p.model}`);
  const maxTokens = Number(p.max_tokens);
  if (Number.isFinite(maxTokens) && maxTokens > 0) {
    // Output budget cap: clone the catalog entry so a constrained run never
    // requests more than the caller allows (also caps provider-side billing).
    model = { ...model, maxTokens: Math.min(maxTokens, (model as any).maxTokens ?? maxTokens) };
  }

  // Resuming an existing transcript keeps the conversation when the engine
  // process was restarted between two turns of one BriefLoop session.
  const resuming = !!sessionFile && existsSync(sessionFile);
  const sessionManager = resuming
    ? SessionManager.open(sessionFile!, sessionDir, cwd)
    : SessionManager.create(cwd, sessionDir);

  const retryDelay = Number(p.retry_base_delay_ms);
  // Settings are BriefLoop's, not the user's ~/.pi or anything under the
  // packet: an auditor must not inherit a personal retry or compaction policy.
  // Compaction is off because summarising packet reads mid-review would let
  // the verdict rest on a paraphrase instead of the evidence.
  const settingsManager = SettingsManager.inMemory({
    retry: {
      enabled: true,
      // 2+4+…+64 s: rides out a provider or network outage of about two
      // minutes, which otherwise throws away a long run's work.
      maxRetries: 6,
      baseDelayMs: Number.isFinite(retryDelay) ? Math.max(10, Math.min(10_000, retryDelay)) : 2000,
    },
    compaction: { enabled: false },
  });

  const basePrompt = String(p.system_prompt ?? "").trim();
  if (!basePrompt) throw new Error("system_prompt required: BriefLoop owns the reviewer contract");

  // The entry does not exist yet when tools are built; hooks resolve it lazily.
  let entryRef: SessionEntry | undefined;
  let modelRef = model;
  const packet = packetTools(packetRoot, {
    admit: (review) => requestAdmission(clientSid, entryRef, review),
    accept: (review) => { if (entryRef) entryRef.submitted = JSON.stringify(review); },
  }, () => acceptsImages(modelRef), p.admission === "runner").filter((t) => local.includes(t.name));
  const fromRunner = runnerTools(declared, {
    call: (tool, args) => requestRunnerTool(clientSid, entryRef, tool, args),
    acceptsImages: () => acceptsImages(modelRef),
    settle: (value) => { if (entryRef) entryRef.submitted = value; },
  });
  const tools = [...packet, ...fromRunner];
  const guides = Object.fromEntries(declared.map((t) => [t.name, t.guide ?? t.description]));
  const systemPrompt = `${basePrompt}\n\n${toolGuide(tools.map((t) => t.name), guides, submitTool || "提交")}`;
  const { session } = await createAgentSession({
    cwd,
    modelRuntime: runtime,
    model,
    thinkingLevel: (p.thinking as any) ?? "high",
    // Explicit allowlist: only our packet tools exist for this session. An
    // empty allowlist disables custom tools too; naming them is what keeps the
    // built-ins (read/bash/edit/write) off.
    tools: tools.map((t) => t.name),
    customTools: tools,
    resourceLoader: fixedLoader(systemPrompt),
    sessionManager,
    settingsManager,
  });
  const active = session.getActiveToolNames().sort();
  const expected = [...local, ...declared.map((t) => t.name)].sort();
  if (active.join(",") !== expected.join(",")) {
    session.dispose();
    throw new Error(`${role} tool confinement check failed: active tools are ${JSON.stringify(active)}`);
  }

  const entry: SessionEntry = {
    session,
    role,
    cwd,
    tools,
    sessionFile: (session as any).sessionFile ?? sessionFile,
    execId: "",
    cancelled: false,
    stalled: false,
    overlong: false,
    replyChars: 0,
    maxReplyChars: DEFAULT_MAX_REPLY_CHARS,
    budgetExceeded: false,
    toolCalls: 0,
    maxToolCalls: Math.max(1, Math.min(300, Number(p.max_tool_calls) || 150)),
    finalText: "",
    submitted: undefined,
    pendingAdmission: new Map(),
    admissionSeq: 0,
    pendingTools: new Map(),
    submitTool,
    turnError: "",
    usage: undefined,
    idleMs: DEFAULT_IDLE_MS,
    idleTimer: undefined,
    unsubscribe: () => {},
  };
  entryRef = entry;
  modelRef = session.model ?? model;
  wireSessionEvents(clientSid, entry);
  sessions.set(clientSid, entry);
  reply(id, {
    // Hash of what pi actually assembled, so a run records the prompt it used.
    system_prompt_sha256: createHash("sha256").update(session.systemPrompt).digest("hex"),
    image_input: acceptsImages(modelRef),
    session_id: clientSid,
    session_file: entry.sessionFile,
    resumed: resuming,
    model: `${model.provider}/${model.id}`,
    thinking: p.thinking ?? "high",
    tools: active,
  });
}

async function turnStart(id: string | undefined, p: Record<string, unknown>): Promise<void> {
  const sid = String(p.session_id ?? "");
  const entry = sessions.get(sid);
  if (!entry) throw new Error("unknown session_id");
  if (entry.execId) throw new Error("session busy");
  const execId = String(p.execution_id ?? "");
  const prompt = String(p.prompt ?? "");
  if (!execId || !prompt) throw new Error("execution_id and prompt required");

  const expectJson = p.expect_json === true;
  const requireSubmit = p.require_submit === true;
  const visual = visualInputs(entry, entry.cwd, p.images);
  const idle = Number(p.idle_timeout_s);
  entry.idleMs = Number.isFinite(idle) && idle > 0 ? Math.min(1800, idle) * 1000 : DEFAULT_IDLE_MS;
  const replyCap = Number(p.max_reply_chars);
  entry.maxReplyChars = Number.isFinite(replyCap) && replyCap > 0 ? replyCap : DEFAULT_MAX_REPLY_CHARS;
  entry.execId = execId;
  entry.cancelled = false;
  entry.budgetExceeded = false;
  entry.toolCalls = 0;
  entry.usage = undefined;
  entry.submitted = undefined;
  reply(id, { started: true, images_attached: visual.images.length });

  let status = "completed";
  let error = "";
  let finalJson: string | undefined;
  let message = prompt + visual.note;
  let images = visual.images;
  let stalls = 0;
  let overlongs = 0;
  let repairs = 0;
  try {
    for (;;) {
      if (entry.cancelled) { status = "cancelled"; break; }
      entry.stalled = false;
      entry.overlong = false;
      entry.turnError = "";
      entry.finalText = "";
      // Armed before the request: the wait for the provider's first byte counts.
      armIdle(sid, entry);
      await entry.session.prompt(message, images.length ? { images } : undefined);
      images = [];
      disarmIdle(entry);
      if (entry.cancelled) { status = "cancelled"; break; }
      // An admitted submission settles the run even if the model's wrap-up
      // request afterwards stalled or failed.
      if (entry.submitted !== undefined) { finalJson = entry.submitted; break; }
      if (entry.budgetExceeded) {
        status = "failed";
        error = `tool-call budget exhausted (${entry.maxToolCalls}); the model did not converge`;
        break;
      }
      if (entry.overlong) {
        if (overlongs < OVERLONG_RETRIES) {
          overlongs += 1;
          emit(sid, execId, "status", { message: `re-asking after an overlong reply (${overlongs}/${OVERLONG_RETRIES})` });
          message = OVERLONG_PROMPT;
          continue;
        }
        status = "failed";
        error = `reply exceeded ${entry.maxReplyChars} characters on ${overlongs + 1} consecutive requests; gave up`;
        break;
      }
      if (entry.stalled) {
        if (stalls < STALL_RETRIES) {
          stalls += 1;
          emit(sid, execId, "status", { message: `re-asking after a stalled request (${stalls}/${STALL_RETRIES})` });
          message = STALL_PROMPT;
          continue;
        }
        status = "failed";
        error = `model stream idle for ${Math.round(entry.idleMs / 1000)}s on ${stalls + 1} consecutive requests; gave up`;
        break;
      }
      if (entry.turnError) { status = "failed"; error = entry.turnError; break; }
      if (requireSubmit) {
        if (repairs < SUBMIT_REPAIRS) {
          repairs += 1;
          emit(sid, execId, "status", { message: `result not submitted; asking for submit_review (${repairs}/${SUBMIT_REPAIRS})` });
          message = submitPrompt(entry.submitTool);
          continue;
        }
        // Last resort: a bare JSON reply still reaches runner admission.
        const parsed = extractJson(entry.finalText);
        if (parsed !== null && typeof parsed === "object") finalJson = JSON.stringify(parsed);
        else { status = "failed"; error = "model finished without submitting a review result"; }
        break;
      }
      if (expectJson) {
        const parsed = extractJson(entry.finalText);
        if (parsed !== null && typeof parsed === "object") {
          finalJson = JSON.stringify(parsed);
        } else if (repairs < JSON_REPAIRS) {
          // The contract is "reply is the JSON object". One in-session
          // correction instead of failing admission on wrapped prose.
          repairs += 1;
          emit(sid, execId, "status", { message: "reply was not a bare JSON object; requesting correction" });
          message = REPAIR_PROMPT;
          continue;
        }
        // Still no object: complete with the raw text; admission judges it.
      }
      break;
    }
  } catch (err) {
    status = entry.cancelled ? "cancelled" : "failed";
    error = err instanceof Error ? err.message : String(err);
  } finally {
    disarmIdle(entry);
    for (const [, settle] of entry.pendingAdmission) settle("审阅执行已结束");
    entry.pendingAdmission.clear();
    for (const [, settle] of entry.pendingTools) settle({ ok: false, error: "执行已结束" });
    entry.pendingTools.clear();
  }
  emit(sid, execId, "end", {
    status,
    aborted: entry.cancelled,
    usage: entry.usage,
    session_file: entry.sessionFile,
    ...(finalJson !== undefined ? { final_text: finalJson } : {}),
    ...(error && status !== "cancelled" ? { error } : {}),
  });
  entry.execId = "";
}

async function turnAbort(id: string | undefined, p: Record<string, unknown>): Promise<void> {
  const entry = sessions.get(String(p.session_id ?? ""));
  if (!entry) throw new Error("unknown session_id");
  entry.cancelled = true;
  disarmIdle(entry);
  await entry.session.abort();
  reply(id, { aborted: true });
}

async function sessionClose(id: string | undefined, p: Record<string, unknown>): Promise<void> {
  const sid = String(p.session_id ?? "");
  const entry = sessions.get(sid);
  if (entry) {
    disarmIdle(entry);
    try { entry.unsubscribe(); } catch {}
    try { entry.session.dispose(); } catch {}
    sessions.delete(sid);
  }
  reply(id, { closed: true });
}

async function dispatch(req: WireRequest): Promise<void> {
  try {
    switch (req.method) {
      case "ping": {
        await ensureRuntime();
        reply(req.id, {
          engine: ENGINE_VERSION,
          pi: PI_VERSION,
          node: process.version,
          // Models whose provider has a credential; zero means nothing can run.
          models_available: registry ? registry.getAvailable().length : 0,
        });
        break;
      }
      case "list_models": {
        await ensureRuntime();
        const models = registry!.getAvailable().map((m) => ({
          id: `${m.provider}/${m.id}`,
          name: m.name ?? m.id,
          provider: m.provider,
          context_window: m.contextWindow,
        }));
        reply(req.id, { models });
        break;
      }
      case "session_create": await sessionCreate(req.id, req.params ?? {}); break;
      case "tool_call": {
        // Deterministic tool invocation for tests/diagnostics — runs the
        // session's own packet tools outside a model turn. Same confinement.
        const entry = sessions.get(String(req.params?.session_id ?? ""));
        if (!entry) throw new Error("unknown session_id");
        const name = String(req.params?.name ?? "");
        const tool = entry.tools.find((t) => t.name === name);
        if (!tool) throw new Error(`no such tool on this session: ${name}`);
        const result = await tool.execute(
          "wire-" + Date.now(), (req.params?.args ?? {}) as never, undefined, undefined, {} as never);
        reply(req.id, {
          content: result.content,
          details: result.details,
        });
        break;
      }
      case "turn_start": await turnStart(req.id, req.params ?? {}); break;
      case "submit_result": {
        const entry = sessions.get(String(req.params?.session_id ?? ""));
        if (!entry) throw new Error("unknown session_id");
        const settle = entry.pendingAdmission.get(String(req.params?.request_id ?? ""));
        if (!settle) throw new Error("no pending submission with that request_id");
        entry.pendingAdmission.delete(String(req.params?.request_id));
        settle(req.params?.ok === true ? undefined : String(req.params?.error || "接纳检查未通过"));
        reply(req.id, { settled: true });
        break;
      }
      case "tool_result": {
        const entry = sessions.get(String(req.params?.session_id ?? ""));
        if (!entry) throw new Error("unknown session_id");
        const requestId = String(req.params?.request_id ?? "");
        const settle = entry.pendingTools.get(requestId);
        if (!settle) throw new Error("no pending tool call with that request_id");
        entry.pendingTools.delete(requestId);
        const p = req.params ?? {};
        settle({
          ok: p.ok === true,
          error: typeof p.error === "string" ? p.error : undefined,
          content: Array.isArray(p.content) ? p.content as RunnerResult["content"] : undefined,
          settle: typeof p.settle === "string" ? p.settle : undefined,
        });
        reply(req.id, { settled: true });
        break;
      }
      case "turn_abort": await turnAbort(req.id, req.params ?? {}); break;
      case "session_close": await sessionClose(req.id, req.params ?? {}); break;
      case "shutdown":
        reply(req.id, { ok: true });
        for (const [, e] of sessions) { try { e.session.dispose(); } catch {} }
        sessions.clear();
        process.exit(0);
        break;
      default:
        replyError(req.id, `unknown method: ${req.method}`);
    }
  } catch (err) {
    replyError(req.id, err instanceof Error ? err.message : String(err));
  }
}

const rl = createInterface({ input: process.stdin, terminal: false });
rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed || !trimmed.startsWith("{")) return;
  let req: WireRequest;
  try { req = JSON.parse(trimmed); } catch { return; }
  void dispatch(req);
});
rl.on("close", () => {
  for (const [, e] of sessions) { try { e.session.dispose(); } catch {} }
  process.exit(0);
});
