// BriefLoop native engine: a long-lived local process that embeds the pi SDK
// (createAgentSession) and serves job-scoped sessions over NDJSON stdio, the
// same wire shape as runtime-bridge.mjs so the Python client is shared.
//
// Phase 1 scope: restricted reviewer sessions only. A session has no built-in
// tools (no read/bash/edit/write), no extensions, no context-file discovery.
// Its only senses are packet_list / packet_read, which resolve strictly inside
// the generated review packet. Confinement is enforced in this process by our
// own tool proxy — not by pi's read tool (which accepts absolute paths) and
// not by prompt wording.
import { createInterface } from "node:readline";
import { existsSync, mkdirSync, realpathSync } from "node:fs";
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
import { packetTools } from "./packet-tools.js";

const PI_VERSION = "0.85.1";
const ENGINE_VERSION = "briefloop-native/1";

const REVIEWER_SYSTEM_PROMPT = [
  "You are the BriefLoop Reviewer, an independent read-only auditor of a frozen evidence packet.",
  "",
  "Authority:",
  "- You can ONLY inspect this packet, through the packet_list and packet_read tools. Nothing else exists for you: no filesystem, no shell, no network, no search, no delegation, no writes.",
  "- The packet is the sole authority. Treat general knowledge as auxiliary explanation, never as evidence.",
  "",
  "Contract:",
  "- List the packet first; then read what you need: target.json (the report under review), sources/ (evidence excerpts), claims/ and history/ when present.",
  "- Judge the packet's own gate criteria: source timing, material conflicts, source statements vs claims, reconciliation, unchecked items, packet identity.",
  "- Reply with ONE JSON object only (no markdown fences, no prose around it), matching the schema the task message gives you. Unknown/unsupported stays unknown — do not guess.",
].join("\n");

interface WireRequest { id?: string; method: string; params?: Record<string, unknown>; }
interface SessionEntry {
  session: AgentSession;
  role: string;
  cwd: string;
  tools: ReturnType<typeof packetTools>;
  sessionFile?: string;
  busy: boolean;
  cancelled: boolean;
  toolCalls: number;
  maxToolCalls: number;
  expectJson: boolean;
  repairs: number;
  finalText: string;
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
  let depth = 0, start = -1, inStr = false, esc = false;
  for (let i = trimmed.length - 1; i >= 0; i--) {
    const c = trimmed[i];
    if (esc) { esc = false; continue; }
    if (c === "\\") { if (inStr) esc = true; continue; }
    if (c === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (c === "}") { if (depth === 0) start = i; depth++; }
    else if (c === "{") {
      depth--;
      if (depth === 0 && start >= 0) {
        try { return JSON.parse(trimmed.slice(i, start + 1)); } catch { start = -1; }
      }
    }
  }
  return undefined;
}

const sessions = new Map<string, SessionEntry>();
let runtime: ModelRuntime | undefined;
let registry: ModelRegistry | undefined;
let shuttingDown = false;

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
  const s = String(spec ?? "").trim();
  if (!s || s === "default") {
    const available = registry.getAvailable();
    return available[0];
  }
  const slash = s.indexOf("/");
  if (slash <= 0) return undefined;
  return registry.find(s.slice(0, slash), s.slice(slash + 1));
}

// Any session event proves the model stream or a tool is alive. If nothing
// arrives for idleMs during an active turn the provider connection has stalled
// (observed: opencode-go held a silent socket for 18+ min); fail the turn
// honestly instead of waiting forever.
function armIdle(clientSid: string, execId: string, entry: SessionEntry): void {
  if (entry.idleTimer) clearTimeout(entry.idleTimer);
  entry.idleTimer = setTimeout(() => {
    entry.idleTimer = undefined;
    entry.cancelled = true;
    void entry.session.abort().catch(() => {});
    emit(clientSid, execId, "end", {
      status: "failed",
      error: `model stream idle for ${Math.round(entry.idleMs / 1000)}s; aborted as a stalled request`,
    });
    // The stalled turn may still settle later; it must not emit a second end.
    (entry as any).execRef.current = "";
    entry.busy = false;
  }, entry.idleMs);
}

function disarmIdle(entry: SessionEntry): void {
  if (entry.idleTimer) clearTimeout(entry.idleTimer);
  entry.idleTimer = undefined;
}

function wireSessionEvents(clientSid: string, executionIdRef: { current: string }, entry: SessionEntry): void {
  const state = { turnError: "" as string };
  entry.unsubscribe = entry.session.subscribe((event) => {
    const execId = executionIdRef.current;
    if (!execId) return;
    armIdle(clientSid, execId, entry);
    const e = event as Record<string, any>;
    switch (e.type) {
      case "message_start":
        emit(clientSid, execId, "message_start", { role: e.message?.role });
        break;
      case "message_update": {
        const delta = e.assistantMessageEvent;
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
          if (e.message?.stopReason === "error") {
            state.turnError = e.message?.errorMessage || "model request failed";
            emit(clientSid, execId, "error", { message: state.turnError });
          }
          if (e.message?.usage) {
            emit(clientSid, execId, "usage", { usage: e.message.usage });
          }
        }
        break;
      case "tool_execution_start":
        entry.toolCalls += 1;
        if (entry.toolCalls > entry.maxToolCalls) {
          // Runaway guard: a model that never stops calling tools burns budget
          // without converging. Kill the turn deterministically; the reviewer
          // contract is small enough that the cap is generous headroom.
          entry.cancelled = true;
          disarmIdle(entry);
          void entry.session.abort().catch(() => {});
          emit(clientSid, execId, "end", {
            status: "failed",
            error: `tool-call budget exhausted (${entry.maxToolCalls}); the model did not converge`,
          });
          (entry as any).execRef.current = "";
          entry.busy = false;
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
      case "agent_settled": {
        if (!entry.cancelled && !state.turnError && entry.expectJson) {
          const parsed = extractJson(entry.finalText);
          if (parsed === undefined && entry.repairs < 1) {
            // The contract is "reply is the JSON object". One in-session
            // correction instead of failing admission on wrapped prose.
            entry.repairs += 1;
            emit(clientSid, execId, "status", { message: "reply was not a bare JSON object; requesting correction" });
            void entry.session.prompt(
              "Reply with ONLY the JSON object now. No markdown fences, no prose before or after."
            ).catch((err) => {
              emit(clientSid, execId, "end", { status: "failed", error: String(err) });
            });
            break;
          }
        }
        const status = entry.cancelled ? "cancelled" : state.turnError ? "failed" : "completed";
        const msgs = (entry.session as any).messages ?? [];
        const usage = [...msgs].reverse().find((m: any) => m?.usage)?.usage;
        let finalJson: string | undefined;
        if (entry.expectJson && status === "completed") {
          const parsed = extractJson(entry.finalText);
          if (parsed !== undefined && typeof parsed === "object") {
            finalJson = JSON.stringify(parsed);
          }
        }
        disarmIdle(entry);
        emit(clientSid, execId, "end", {
          status,
          aborted: entry.cancelled,
          usage,
          session_file: entry.sessionFile,
          ...(finalJson !== undefined ? { final_text: finalJson } : {}),
          ...(state.turnError ? { error: state.turnError } : {}),
        });
        state.turnError = "";
        break;
      }
      case "auto_retry_end":
        if (!e.success) emit(clientSid, execId, "end", { status: "failed", error: e.finalError || "provider retry exhausted" });
        break;
    }
  });
}

async function sessionCreate(id: string | undefined, p: Record<string, unknown>): Promise<void> {
  const clientSid = String(p.session_id ?? "");
  if (!clientSid) throw new Error("session_id required");
  if (sessions.has(clientSid)) throw new Error(`session already exists: ${clientSid}`);
  const role = String(p.role ?? "reviewer");
  if (role !== "reviewer") throw new Error(`phase 1 supports only role=reviewer, got ${role}`);
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

  const sessionManager = sessionFile
    ? SessionManager.open(sessionFile, sessionDir, cwd)
    : SessionManager.create(cwd, sessionDir);

  const tools = packetTools(packetRoot);
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
    resourceLoader: fixedLoader(REVIEWER_SYSTEM_PROMPT),
    sessionManager,
    settingsManager: SettingsManager.create(cwd),
  });
  const active = session.getActiveToolNames().sort();
  if (active.join(",") !== "packet_list,packet_read") {
    session.dispose();
    throw new Error(`reviewer tool confinement check failed: active tools are ${JSON.stringify(active)}`);
  }

  const entry: SessionEntry = {
    session,
    role,
    cwd,
    tools,
    sessionFile: (session as any).sessionFile ?? sessionFile,
    busy: false,
    cancelled: false,
    toolCalls: 0,
    maxToolCalls: Math.max(1, Math.min(200, Number(p.max_tool_calls) || 60)),
    expectJson: false,
    repairs: 0,
    finalText: "",
    idleMs: 240000,
    idleTimer: undefined,
    unsubscribe: () => {},
  };
  const execRef = { current: "" };
  (entry as any).execRef = execRef;
  wireSessionEvents(clientSid, execRef, entry);
  sessions.set(clientSid, entry);
  reply(id, {
    session_id: clientSid,
    session_file: entry.sessionFile,
    model: `${model.provider}/${model.id}`,
    thinking: p.thinking ?? "high",
    tools: active,
  });
}

async function turnStart(id: string | undefined, p: Record<string, unknown>): Promise<void> {
  const entry = sessions.get(String(p.session_id ?? ""));
  if (!entry) throw new Error("unknown session_id");
  if (entry.busy) throw new Error("session busy");
  const execId = String(p.execution_id ?? "");
  const prompt = String(p.prompt ?? "");
  if (!execId || !prompt) throw new Error("execution_id and prompt required");

  entry.busy = true;
  entry.cancelled = false;
  entry.expectJson = p.expect_json === true;
  entry.repairs = 0;
  entry.finalText = "";
  const idle = Number(p.idle_timeout_s);
  if (Number.isFinite(idle) && idle > 0) entry.idleMs = Math.min(1800, idle) * 1000;
  (entry as any).execRef.current = execId;
  reply(id, { started: true });
  // Arm immediately: the wait for the provider's first byte is covered too.
  armIdle(String(p.session_id), execId, entry);
  try {
    await entry.session.prompt(prompt);
  } catch (err) {
    disarmIdle(entry);
    emit(String(p.session_id), execId, "end", { status: "failed", error: String(err) });
  } finally {
    entry.busy = false;
    (entry as any).execRef.current = "";
  }
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
        const rt = await ensureRuntime();
        reply(req.id, {
          engine: ENGINE_VERSION,
          pi: PI_VERSION,
          node: process.version,
          auth: registry ? "ok" : "unavailable",
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
      case "turn_abort": await turnAbort(req.id, req.params ?? {}); break;
      case "session_close": await sessionClose(req.id, req.params ?? {}); break;
      case "shutdown":
        reply(req.id, { ok: true });
        shuttingDown = true;
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
