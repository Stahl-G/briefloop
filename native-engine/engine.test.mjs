// Engine wire-protocol, isolation and turn-lifecycle checks against the built
// bundle. No real model and no machine credentials: the bundle is copied into
// a temp dir beside a models.json that points at a scripted OpenAI-compatible
// server, and HOME is a temp dir so ~/.pi auth or settings cannot leak in.
// Run: node --test native-engine/engine.test.mjs
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const BUNDLE = fileURLToPath(new URL("../src/briefloop/static/native-engine.mjs", import.meta.url));
const nodeBin = process.env.BRIEFLOOP_NODE || process.execPath;
const MODEL = "fake/m1";

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
async function reviewer(extra = {}) {
  const session_id = "s" + ++sessions;
  const res = await call("session_create", {
    session_id, role: "reviewer", packet_root: packet, session_dir: join(root, "sessions"),
    model: MODEL, thinking: "low", retry_base_delay_ms: 10, ...extra });
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
    models: [{ id: "m1", name: "M1", api: "openai-completions", provider: "fake", reasoning: false, input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 100000, maxTokens: 1000 }] } } }));

  packet = mkdtempSync(join(root, "packet-"));
  mkdirSync(join(packet, "sources"));
  writeFileSync(join(packet, "index.json"), JSON.stringify({ fingerprint: "abc123" }));
  writeFileSync(join(packet, "target.json"), "line1\nline2\nline3\nline4\nline5\n");
  writeFileSync(join(packet, "sources", "src1.txt"), "evidence text");
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
      if (msg.method === "event") { events.push(msg.params); for (const l of [...listeners]) l(); }
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
  assert.equal(ping.engine, "briefloop-native/1");
  assert.equal(ping.models_available, 1);
});

test("session_create refuses non-reviewer roles and models without a provider", async () => {
  assert.match(await callError("session_create", { session_id: "x1", role: "writer", packet_root: packet, model: MODEL }), /reviewer/);
  assert.match(await callError("session_create", { session_id: "x2", role: "reviewer", packet_root: packet, model: "m1" }), /unknown or unavailable model/);
});

test("reviewer session exposes exactly the packet tools and confines reads", async () => {
  const { session_id, tools, session_file } = await reviewer();
  assert.deepEqual(tools, ["packet_list", "packet_read"]);
  assert.ok(session_file, "session file path reported for audit");
  const tool = (name, args) => call("tool_call", { session_id, name, args });
  const text = (r) => r.content.find((c) => c.type === "text").text;

  const list = text(await tool("packet_list", {}));
  assert.match(list, /index\.json/);
  assert.match(list, /sources\/src1\.txt/);
  assert.doesNotMatch(list, /secret/);
  assert.equal(text(await tool("packet_read", { path: "sources/src1.txt" })), "evidence text");
  const range = text(await tool("packet_read", { path: "target.json", start_line: 2, end_line: 3 }));
  assert.match(range, /lines 2-3 of 6/);
  assert.doesNotMatch(range, /line1|line4/);

  const escapes = [join(outside, "secret.txt"), "../" + outside.split(/[\\/]/).pop() + "/secret.txt", "../../etc/hosts"];
  if (process.platform !== "win32") escapes.push("linked-secret.txt");
  for (const path of escapes) {
    const msg = await callError("tool_call", { session_id, name: "packet_read", args: { path } });
    assert.doesNotMatch(msg, /OUTSIDE_SECRET/, path);
  }
  assert.match(await callError("tool_call", { session_id, name: "bash", args: {} }), /no such tool/);
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
