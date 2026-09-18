// Engine wire-protocol, isolation and turn-lifecycle checks against the built
// bundle. No real model and no machine credentials: the bundle is copied into
// a temp dir beside a models.json that points at a scripted OpenAI-compatible
// server, and HOME is a temp dir so ~/.pi auth or settings cannot leak in.
// Run: node --test native-engine/engine.test.mjs
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const BUNDLE = fileURLToPath(new URL("../src/briefloop/static/native-engine.mjs", import.meta.url));
const nodeBin = process.env.BRIEFLOOP_NODE || process.execPath;
const MODEL = "fake/m1";
const VISION_MODEL = "fake/m2";
const SYSTEM = "测试系统提示：BriefLoop Reviewer";
// A 1x1 PNG, enough for pi to carry it as image content.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

// ---- scripted provider ------------------------------------------------------
const provider = { script: [], requests: [], violations: [], stalled: new Set() };
const chunk = (res, delta, finish = null, usage) => res.write(`data: ${JSON.stringify({
  id: "c", object: "chat.completion.chunk", created: 0, model: "m1",
  choices: [{ index: 0, delta, finish_reason: finish }], ...(usage ? { usage } : {}) })}\n\n`);
const usage = { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5 };
const reply = {
  text: (text) => (res) => {
    res.writeHead(200, { "content-type": "text/event-stream" });
    chunk(res, { role: "assistant", content: text });
    chunk(res, {}, "stop", usage);
    res.end("data: [DONE]\n\n");
  },
  tool: (name, args = {}) => (res) => {
    if (typeof args === "function") args = args();
    res.writeHead(200, { "content-type": "text/event-stream" });
    const id = "call_" + Math.random().toString(36).slice(2, 10);
    chunk(res, { role: "assistant", tool_calls: [{ index: 0, id, type: "function", function: { name, arguments: JSON.stringify(args) } }] });
    chunk(res, {}, "tool_calls", usage);
    res.end("data: [DONE]\n\n");
  },
  stall: () => (res) => { res.writeHead(200, { "content-type": "text/event-stream" }); provider.stalled.add(res); },
  status: (code) => (res) => {
    res.writeHead(code, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { message: `scripted ${code}` } }));
  },
};

// A provider rejects an assistant tool call that is not answered by tool results;
// resuming after an aborted request must never send such a history.
function sequenceViolation(messages) {
  for (let i = 0; i < messages.length; i++) {
    const calls = messages[i].role === "assistant" ? messages[i].tool_calls || [] : [];
    const answered = new Set();
    for (let j = i + 1; j < messages.length && messages[j].role === "tool"; j++) answered.add(messages[j].tool_call_id);
    const missing = calls.filter((c) => !answered.has(c.id));
    if (missing.length) return `unanswered tool call ${missing[0].id}`;
  }
  return "";
}

const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (d) => (body += d));
  req.on("end", () => {
    const parsed = JSON.parse(body || "{}");
    provider.requests.push(parsed);
    const violation = sequenceViolation(parsed.messages || []);
    if (violation) {
      provider.violations.push(violation);
      res.writeHead(400, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: { message: violation } }));
    }
    const step = provider.script.length > 1 ? provider.script.shift() : provider.script[0];
    (step || reply.status(500))(res);
  });
});
function script(...steps) {
  provider.script = steps;
  provider.requests = [];
  provider.violations = [];
}

// ---- engine wire ------------------------------------------------------------
let proc, root, packet, outside;
let buf = "";
const waiters = new Map();
const events = [];
const listeners = new Set();
let seq = 0;

function call(method, params = {}, timeout = 30000) {
  return new Promise((resolve, reject) => {
    const id = "t" + ++seq;
    const timer = setTimeout(() => { waiters.delete(id); reject(new Error(method + " timeout")); }, timeout);
    waiters.set(id, (msg) => { clearTimeout(timer); msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result); });
    proc.stdin.write(JSON.stringify({ id, method, params }) + "\n");
  });
}
async function callError(method, params = {}) {
  try { await call(method, params); } catch (e) { return e.message; }
  throw new Error(method + " should have failed: " + JSON.stringify(params));
}
// Resolves with every event of the execution once its first `end` arrived and
// a grace period passed, so a duplicate terminal event is caught too.
function settle(execId, timeout = 20000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { listeners.delete(check); reject(new Error(`no end for ${execId}`)); }, timeout);
    const check = () => {
      if (!events.some((e) => e.execution_id === execId && e.kind === "end")) return;
      listeners.delete(check);
      clearTimeout(timer);
      setTimeout(() => resolve(events.filter((e) => e.execution_id === execId)), 400);
    };
    listeners.add(check);
    check();
  });
}
const ends = (evts) => evts.filter((e) => e.kind === "end");
let sessions = 0;
let admission = () => undefined;
async function reviewer(extra = {}) {
  const session_id = "s" + ++sessions;
  const res = await call("session_create", {
    session_id, role: "reviewer", packet_root: packet, session_dir: join(root, "sessions"),
    model: MODEL, thinking: "low", retry_base_delay_ms: 10, system_prompt: SYSTEM, ...extra });
  return { session_id, ...res };
}
async function turn(session_id, execution_id, extra = {}) {
  const done = settle(execution_id);
  await call("turn_start", { session_id, execution_id, prompt: "review the packet", expect_json: true, idle_timeout_s: 1, ...extra });
  return done;
}

before(async () => {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  root = mkdtempSync(join(tmpdir(), "bl-native-test-"));
  const engineDir = join(root, "engine");
  const home = join(root, "home");
  mkdirSync(engineDir);
  mkdirSync(join(home, ".pi", "agent"), { recursive: true });
  // A personal pi setting that would break retries if the engine honoured it.
  writeFileSync(join(home, ".pi", "agent", "settings.json"), JSON.stringify({ retry: { enabled: false } }));
  copyFileSync(BUNDLE, join(engineDir, "native-engine.mjs"));
  writeFileSync(join(engineDir, "native-engine-models.json"), JSON.stringify({ providers: { fake: {
    baseUrl: `http://127.0.0.1:${server.address().port}/v1`, api: "openai-completions", apiKey: "FAKE_PROVIDER_KEY",
    models: [
      { id: "m1", name: "M1", api: "openai-completions", provider: "fake", reasoning: false, input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 100000, maxTokens: 1000 },
      { id: "m2", name: "M2", api: "openai-completions", provider: "fake", reasoning: false, input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 100000, maxTokens: 1000 },
    ] } } }));

  packet = mkdtempSync(join(root, "packet-"));
  mkdirSync(join(packet, "sources"));
  writeFileSync(join(packet, "index.json"), JSON.stringify({ fingerprint: "abc123" }));
  writeFileSync(join(packet, "notes.txt"), "line1\nline2\nline3\nline4\nline5\n");
  writeFileSync(join(packet, "sources", "src1.txt"), "evidence text");
  writeFileSync(join(packet, "sources", "src2.view.json"), "{\"lines\": [\"现金及受限现金 5,890 万美元\"]}\n第二行 [ $85.9 ] million\n");
  writeFileSync(join(packet, "sources", "src2.pdf"), "%PDF-1.4 现金");
  mkdirSync(join(packet, "figures"));
  writeFileSync(join(packet, "figures", "fig1.png"), PNG);
  writeFileSync(join(packet, "claims.json"), "{}");
  writeFileSync(join(packet, "output.schema.json"), JSON.stringify({
    type: "object", required: ["status", "version_id"], additionalProperties: false,
    properties: { status: { enum: ["complete", "incomplete"] }, version_id: { type: "string" } } }));
  writeFileSync(join(packet, "target.json"), JSON.stringify({
    evidence: { bindings: [{ claim_id: "claim_a", block_id: "block_1",
      claim: { data: { statement: "现金 5,890 万美元", supports: [{ span_id: "span_1" }] } },
      evidence: [{ id: "span_1", data: { excerpt: "58.9 million", located_text: "cash of $58.9 million", excerpt_hash: "h" } }],
      premises: [{ claim_id: "claim_p", claim: { data: { statement: "前提" } }, evidence: [], premises: [] }] }] },
    candidate_claims: [],
    document: { type: "doc", content: [{ type: "paragraph", attrs: { blockId: "block_1" }, content: [{ type: "text", text: "年末现金 5,890 万美元。" }] }] },
  }));
  outside = mkdtempSync(join(root, "outside-"));
  writeFileSync(join(outside, "secret.txt"), "OUTSIDE_SECRET_7f3a");
  if (process.platform !== "win32") symlinkSync(join(outside, "secret.txt"), join(packet, "linked-secret.txt"));

  proc = spawn(nodeBin, [join(engineDir, "native-engine.mjs")], {
    stdio: ["pipe", "pipe", "inherit"],
    env: { PATH: process.env.PATH, HOME: home, USERPROFILE: home, FAKE_PROVIDER_KEY: "k", NO_PROXY: "*" },
  });
  proc.stdout.on("data", (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      const msg = JSON.parse(line);
      if (msg.method === "event") {
        events.push(msg.params);
        // Stands in for the Python runner's admission check.
        if (msg.params.kind === "submit") {
          const error = admission(msg.params.review);
          void call("submit_result", { session_id: msg.params.session_id, request_id: msg.params.request_id, ok: !error, error });
        }
        for (const l of [...listeners]) l();
      }
      else if (msg.id && waiters.has(msg.id)) { waiters.get(msg.id)(msg); waiters.delete(msg.id); }
    }
  });
});

after(async () => {
  for (const res of provider.stalled) res.destroy();
  try { await call("shutdown", {}, 5000); } catch {}
  proc.kill();
  server.close();
  rmSync(root, { recursive: true, force: true });
});

// ---- protocol and isolation ---------------------------------------------------
test("ping reports the engine and credentialed models", async () => {
  const ping = await call("ping");
  assert.equal(ping.engine, "briefloop-native/2");
  assert.equal(ping.models_available, 2);
});

test("session_create refuses non-reviewer roles and models without a provider", async () => {
  assert.match(await callError("session_create", { session_id: "x1", role: "writer", packet_root: packet, model: MODEL }), /reviewer/);
  assert.match(await callError("session_create", { session_id: "x2", role: "reviewer", packet_root: packet, model: "m1", system_prompt: SYSTEM }), /unknown or unavailable model/);
  assert.match(await callError("session_create", { session_id: "x3", role: "reviewer", packet_root: packet, model: MODEL }), /system_prompt required/);
});

test("reviewer session exposes exactly the packet tools and confines reads", async () => {
  const { session_id, tools, session_file } = await reviewer();
  assert.deepEqual(tools, ["calc", "claim_trace", "packet_grep", "packet_list", "packet_read", "submit_review"]);
  assert.ok(session_file, "session file path reported for audit");
  const tool = (name, args) => call("tool_call", { session_id, name, args });
  const text = (r) => r.content.find((c) => c.type === "text").text;

  const list = text(await tool("packet_list", {}));
  assert.match(list, /index\.json/);
  assert.match(list, /sources\/src1\.txt/);
  assert.doesNotMatch(list, /secret/);
  assert.equal(text(await tool("packet_read", { path: "sources/src1.txt" })), "evidence text");
  const range = text(await tool("packet_read", { path: "notes.txt", start_line: 2, end_line: 3 }));
  assert.match(range, /第 2-3 行，共 6 行/);
  assert.doesNotMatch(range, /line1|line4/);

  const escapes = [join(outside, "secret.txt"), "../" + outside.split(/[\\/]/).pop() + "/secret.txt", "../../etc/hosts"];
  if (process.platform !== "win32") escapes.push("linked-secret.txt");
  for (const path of escapes) {
    const msg = await callError("tool_call", { session_id, name: "packet_read", args: { path } });
    assert.doesNotMatch(msg, /OUTSIDE_SECRET/, path);
  }
  assert.match(await callError("tool_call", { session_id, name: "bash", args: {} }), /no such tool/);
  // Search never leaves the packet either: an escaping prefix is refused and
  // the linked secret outside the packet is not searched.
  assert.match(await callError("tool_call", { session_id, name: "packet_grep", args: { pattern: "OUTSIDE", path: "../" } }), /相对路径/);
  assert.match(text(await tool("packet_grep", { pattern: "OUTSIDE_SECRET" })), /没有命中/);
  await callError("tool_call", { session_id: "nope", name: "packet_read", args: { path: "index.json" } });
  await call("session_close", { session_id });
  await callError("tool_call", { session_id, name: "packet_list", args: {} });
});

// ---- turn lifecycle -------------------------------------------------------------
test("a JSON reply completes with one end carrying the object", async () => {
  script(reply.text('{"ok":true}'));
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-json");
  assert.equal(ends(evts).length, 1);
  assert.equal(ends(evts)[0].status, "completed");
  assert.equal(ends(evts)[0].final_text, '{"ok":true}');
});

test("prose then JSON: the correction reply is delivered, not dropped", async () => {
  script(reply.text("Here is my review, everything is fine."), reply.text('{"verdict":"fine"}'));
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-repair");
  assert.equal(provider.requests.length, 2);
  assert.ok(evts.some((e) => e.kind === "status" && /requesting correction/.test(e.message)));
  assert.equal(ends(evts).length, 1);
  assert.equal(ends(evts)[0].status, "completed");
  assert.equal(ends(evts)[0].final_text, '{"verdict":"fine"}');
});

test("JSON with escaped quotes inside narration is still extracted", async () => {
  // One escaped quote: scanning backwards pairs it with the wrong delimiter.
  script(reply.text('Result follows: {"note":"a 5\\" screen {not a brace}"} done'));
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-escaped");
  assert.equal(provider.requests.length, 1, "no correction round for a recoverable object");
  assert.equal(JSON.parse(ends(evts)[0].final_text).note, 'a 5" screen {not a brace}');
});

test("a stalled request is re-asked in the same session and keeps tool results", async () => {
  script(reply.tool("packet_read", { path: "sources/src1.txt" }), reply.stall(), reply.text('{"after":"stall"}'));
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-stall");
  assert.deepEqual(provider.violations, []);
  assert.equal(provider.requests.length, 3);
  const resumed = provider.requests[2].messages;
  assert.ok(resumed.some((m) => m.role === "tool" && /evidence text/.test(JSON.stringify(m.content))),
    "the re-asked request still carries the packet read");
  assert.ok(evts.some((e) => e.kind === "status" && /re-asking after a stalled request \(1\/2\)/.test(e.message)));
  assert.equal(ends(evts).length, 1);
  assert.equal(ends(evts)[0].status, "completed");
  assert.equal(ends(evts)[0].final_text, '{"after":"stall"}');
});

test("repeated stalls fail the turn once, and the session stays usable", async () => {
  script(reply.stall());
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-stall-forever");
  assert.equal(provider.requests.length, 3);
  assert.equal(ends(evts).length, 1);
  assert.equal(ends(evts)[0].status, "failed");
  assert.match(ends(evts)[0].error, /idle for 1s on 3 consecutive requests/);

  script(reply.text('{"second":true}'));
  const next = await turn(session_id, "e-after-stall");
  assert.equal(ends(next).length, 1);
  assert.equal(ends(next)[0].status, "completed");
});

test("provider errors that exhaust retries end once as failed", async () => {
  script(reply.status(500));
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-500", { idle_timeout_s: 30 });
  assert.equal(provider.requests.length, 4, "one request plus three retries");
  assert.equal(ends(evts).length, 1);
  assert.equal(ends(evts)[0].status, "failed");
  assert.match(ends(evts)[0].error, /500/);
});

test("retry policy is BriefLoop's, not the user's ~/.pi settings", async () => {
  script(reply.status(503), reply.text('{"retried":true}'));
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-retry", { idle_timeout_s: 30 });
  assert.equal(ends(evts)[0].status, "completed");
  assert.equal(ends(evts)[0].final_text, '{"retried":true}');
});

test("a runaway tool loop is stopped with one failed end", async () => {
  script(reply.tool("packet_list"));
  const { session_id } = await reviewer({ max_tool_calls: 2 });
  const evts = await turn(session_id, "e-budget", { idle_timeout_s: 30 });
  assert.equal(ends(evts).length, 1);
  assert.equal(ends(evts)[0].status, "failed");
  assert.match(ends(evts)[0].error, /tool-call budget exhausted \(2\)/);
});

test("cancelling during a stall ends once as cancelled", async () => {
  script(reply.stall());
  const { session_id } = await reviewer();
  const done = settle("e-cancel");
  await call("turn_start", { session_id, execution_id: "e-cancel", prompt: "review", expect_json: true, idle_timeout_s: 30 });
  await new Promise((r) => setTimeout(r, 300));
  await call("turn_abort", { session_id });
  const evts = await done;
  assert.equal(ends(evts).length, 1);
  assert.equal(ends(evts)[0].status, "cancelled");
  assert.equal(provider.requests.length, 1, "a cancelled turn is not re-asked");
});

test("a second turn_start on a busy session is refused", async () => {
  script(reply.stall());
  const { session_id } = await reviewer();
  const done = settle("e-busy");
  await call("turn_start", { session_id, execution_id: "e-busy", prompt: "review", idle_timeout_s: 30 });
  assert.match(await callError("turn_start", { session_id, execution_id: "e-busy-2", prompt: "again" }), /busy/);
  await call("turn_abort", { session_id });
  assert.equal(ends(await done).length, 1);
});

test("a closed session resumes from its session file with the prior conversation", async () => {
  script(reply.text('{"first":true}'));
  const first = await reviewer();
  await turn(first.session_id, "e-resume-1");
  await call("session_close", { session_id: first.session_id });

  script(reply.text('{"second":true}'));
  const resumed = await reviewer({ session_file: first.session_file });
  assert.equal(resumed.resumed, true);
  assert.equal(resumed.session_file, first.session_file);
  const evts = await turn(resumed.session_id, "e-resume-2");
  assert.equal(ends(evts)[0].final_text, '{"second":true}');
  const sent = JSON.stringify(provider.requests[0].messages);
  assert.match(sent, /\{\\"first\\":true\}/, "the earlier reply is part of the resumed context");
});

// ---- review tools ---------------------------------------------------------------
test("the system prompt is BriefLoop's layers plus the real tool guide", async () => {
  script(reply.text('{"ok":true}'));
  const { session_id, system_prompt_sha256, image_input } = await reviewer();
  await turn(session_id, "e-system");
  const system = provider.requests[0].messages.find((m) => m.role === "system" || m.role === "developer");
  const body = typeof system.content === "string" ? system.content : JSON.stringify(system.content);
  assert.ok(body.startsWith(SYSTEM));
  assert.match(body, /## 本次可用工具/);
  for (const name of ["packet_grep", "claim_trace", "calc", "submit_review"]) assert.match(body, new RegExp(`- ${name}：`));
  assert.doesNotMatch(body, /coding assistant|read, bash, edit, write/i);
  assert.match(system_prompt_sha256, /^[0-9a-f]{64}$/);
  assert.equal(image_input, false);
});

test("grep, json_path, claim_trace and calc answer inside the packet", async () => {
  const { session_id } = await reviewer();
  const tool = async (name, args) => (await call("tool_call", { session_id, name, args })).content.find((c) => c.type === "text").text;
  const hits = await tool("packet_grep", { pattern: "85.9", path: "sources/" });
  assert.match(hits, /共 1 处命中/);
  assert.match(hits, /sources\/src2\.view\.json:2: 第二行 \[ \$85\.9 \] million/);
  assert.doesNotMatch(hits, /src2\.pdf/, "binary originals are not searched as text");
  assert.match(await tool("packet_grep", { pattern: "5,8\\d0", regex: true }), /src2\.view\.json:1:/);
  assert.match(await tool("packet_read", { path: "sources/src2.pdf" }), /二进制原件/);

  assert.equal(JSON.parse(await tool("packet_read", { path: "target.json", json_path: "evidence.bindings[0].claim.data.statement" })), "现金 5,890 万美元");
  assert.match(await callError("tool_call", { session_id, name: "packet_read", args: { path: "target.json", json_path: "evidence.nope" } }), /可用字段：bindings/);

  await call("tool_call", { session_id, name: "packet_list", args: {} });
  const traced = JSON.parse(await tool("claim_trace", { claim_id: "claim_a" }));
  assert.equal(traced[0].block_text, "年末现金 5,890 万美元。");
  assert.equal(traced[0].evidence[0].span_id, "span_1");
  assert.equal(traced[0].evidence[0].excerpt_hash, undefined);
  assert.deepEqual(traced[0].premises, ["claim_p"]);

  assert.equal(await tool("calc", { expression: "(58.9-85.9)/85.9*100" }), "(58.9-85.9)/85.9*100 = -31.4318975553");
  assert.equal(await tool("calc", { expression: "round(1,034,000/2 ; 0) + 10%" }), "round(1,034,000/2 ; 0) + 10% = 517000.1");
  assert.match(await callError("tool_call", { session_id, name: "calc", args: { expression: "process.exit(1)" } }), /无法识别/);
});

test("submit_review rejects schema and admission errors, then settles the run", async () => {
  let rejections = 0;
  admission = (review) => (review.version_id !== "v1" && ++rejections ? "Reviewer 输出未绑定本次正文与核查包" : undefined);
  script(
    reply.tool("submit_review", { review: { status: "done", basis: "x" } }),
    reply.tool("submit_review", { review: { status: "complete", version_id: "v0" } }),
    reply.tool("submit_review", { review: { status: "complete", version_id: "v1" } }),
    reply.text("已提交。"),
  );
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-submit", { require_submit: true, idle_timeout_s: 30 });
  admission = () => undefined;
  const toolResults = provider.requests.flatMap((r) => r.messages).filter((m) => m.role === "tool").map((m) => JSON.stringify(m.content));
  assert.ok(toolResults.some((t) => /结构校验未通过/.test(t) && /additionalProperties|basis/.test(t)), "schema errors go back to the model");
  assert.ok(toolResults.some((t) => /接纳检查未通过/.test(t) && /未绑定本次正文/.test(t)), "runner admission errors go back to the model");
  assert.equal(rejections, 1);
  assert.equal(evts.filter((e) => e.kind === "submit").length, 2, "only schema-valid results reach the runner");
  assert.equal(ends(evts).length, 1);
  assert.equal(ends(evts)[0].status, "completed");
  assert.deepEqual(JSON.parse(ends(evts)[0].final_text), { status: "complete", version_id: "v1" });
});

test("a run that never submits is asked to, then ends from the submission", async () => {
  script(reply.text("审阅完成，结论如下……"), reply.tool("submit_review", { review: { status: "incomplete", version_id: "v1" } }), reply.text("好"));
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-nosubmit", { require_submit: true, idle_timeout_s: 30 });
  assert.ok(evts.some((e) => e.kind === "status" && /asking for submit_review \(1\/2\)/.test(e.message)));
  assert.equal(ends(evts)[0].status, "completed");
  assert.equal(JSON.parse(ends(evts)[0].final_text).status, "incomplete");
});

test("report figures are attached only when the model accepts images", async () => {
  const sha256 = createHash("sha256").update(PNG).digest("hex");
  const images = [{ id: "figure:f1", kind: "report_figure", title: "图1", file: "figures/fig1.png", mime: "image/png", sha256 }];

  script(reply.text('{"ok":true}'));
  const textOnly = await reviewer();
  const plain = await turn(textOnly.session_id, "e-img-text", { images });
  const sentPlain = JSON.stringify(provider.requests[0].messages);
  assert.doesNotMatch(sentPlain, /image_url/);
  assert.match(sentPlain, /not_sent_model_text_only/);
  assert.equal(ends(plain)[0].status, "completed");
  const readImage = (await call("tool_call", { session_id: textOnly.session_id, name: "packet_read", args: { path: "figures/fig1.png" } })).content;
  assert.ok(readImage.every((c) => c.type === "text") && /不接收图像/.test(readImage[0].text));

  script(reply.text('{"ok":true}'));
  const vision = await reviewer({ model: VISION_MODEL });
  assert.equal(vision.image_input, true);
  await turn(vision.session_id, "e-img-vision", { images });
  const sent = JSON.stringify(provider.requests[0].messages);
  assert.match(sent, /image_url/);
  assert.match(sent, /"delivery\\":\\"attached\\"/);

  const tampered = [{ ...images[0], sha256: "0".repeat(64) }];
  const bad = await reviewer({ model: VISION_MODEL });
  assert.match(await callError("turn_start", { session_id: bad.session_id, execution_id: "e-img-bad", prompt: "x", images: tampered }), /changed before sending/);
});

test("one grep call answers several patterns and one read call several pieces", async () => {
  const { session_id } = await reviewer();
  const tool = async (name, args) => (await call("tool_call", { session_id, name, args })).content.filter((c) => c.type === "text").map((c) => c.text).join("\n");
  const grep = await tool("packet_grep", { patterns: ["85.9", "evidence text", "absent-term"], path: "sources/" });
  assert.match(grep, /=== 85\.9 ===\n共 1 处命中/);
  assert.match(grep, /=== evidence text ===\n共 1 处命中/);
  assert.match(grep, /=== absent-term ===\n没有命中/);
  assert.match(grep, /src2\.view\.json-1- /, "default context shows the neighbouring line");
  const read = await tool("packet_read", { path: "sources/src1.txt", more: [{ path: "notes.txt", start_line: 2, end_line: 2 }, { path: "../escape.txt" }] });
  assert.match(read, /=== sources\/src1\.txt ===\nevidence text/);
  assert.match(read, /=== notes\.txt 第 2-2 行 ===\n\[第 2-2 行，共 6 行\]\nline2/);
  assert.match(read, /=== \.\.\/escape\.txt ===\n读取失败：/, "a bad piece fails alone and never escapes the packet");
  assert.match(await callError("tool_call", { session_id, name: "packet_grep", args: {} }), /至少给一个/);
});

test("a rejected submission is fixed by a patch of the failing fields", async () => {
  script(
    reply.tool("submit_review", { review: { status: "complete", version_id: "v0" } }),
    reply.tool("submit_review", { patch: { version_id: "v1" } }),
    reply.text("好"),
  );
  admission = (review) => (review.version_id === "v1" ? undefined : "Reviewer 输出未绑定本次正文与核查包");
  const { session_id } = await reviewer();
  const evts = await turn(session_id, "e-patch", { require_submit: true, idle_timeout_s: 30 });
  admission = () => undefined;
  assert.equal(ends(evts)[0].status, "completed");
  assert.deepEqual(JSON.parse(ends(evts)[0].final_text), { status: "complete", version_id: "v1" });
  const { session_id: fresh } = await reviewer();
  const patchFirst = await call("tool_call", { session_id: fresh, name: "submit_review", args: { patch: { status: "complete" } } }).catch((e) => e.message);
  assert.match(String(patchFirst), /先用 review 提交完整对象/);
});
