// Engine wire-protocol and isolation checks. No model calls — the session's
// own tools are invoked via the `tool_call` wire method, so confinement is
// tested as the engine actually enforces it, not as a unit test of internals.
// Run: node engine.test.mjs  (uses the bundled src/briefloop/static/native-engine.mjs)
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";

const BUNDLE = new URL("../src/briefloop/static/native-engine.mjs", import.meta.url).pathname;
const nodeBin = process.env.BRIEFLOOP_NODE || "node";

// A packet the reviewer may read, plus a file OUTSIDE it that must stay unreadable.
const packet = mkdtempSync(join(tmpdir(), "bl-packet-"));
mkdirSync(join(packet, "sources"));
mkdirSync(join(packet, "history"));
writeFileSync(join(packet, "index.json"), JSON.stringify({ fingerprint: "abc123" }));
writeFileSync(join(packet, "target.json"), "line1\nline2\nline3\nline4\nline5\n");
writeFileSync(join(packet, "sources", "src1.txt"), "evidence text");
const outside = mkdtempSync(join(tmpdir(), "bl-outside-"));
writeFileSync(join(outside, "secret.txt"), "OUTSIDE_SECRET_7f3a");
symlinkSync(join(outside, "secret.txt"), join(packet, "linked-secret.txt"));

const proc = spawn(nodeBin, [BUNDLE], { stdio: ["pipe", "pipe", "inherit"] });
let buf = "";
const waiters = new Map();
const events = [];
proc.stdout.on("data", (d) => {
  buf += d;
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1);
    if (!line.trim()) continue;
    const msg = JSON.parse(line);
    if (msg.method === "event") events.push(msg.params);
    else if (msg.id && waiters.has(msg.id)) { waiters.get(msg.id)(msg); waiters.delete(msg.id); }
  }
});
let seq = 0;
const call = (method, params = {}, timeout = 30000) =>
  new Promise((resolve, reject) => {
    const id = "t" + ++seq;
    const timer = setTimeout(() => { waiters.delete(id); reject(new Error(method + " timeout")); }, timeout);
    waiters.set(id, (msg) => { clearTimeout(timer); msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result); });
    proc.stdin.write(JSON.stringify({ id, method, params }) + "\n");
  });
const callExpectError = async (method, params = {}) => {
  try { await call(method, params); } catch (e) { return e.message; }
  throw new Error(method + " should have failed: " + JSON.stringify(params));
};

let passed = 0, failed = 0;
async function check(name, fn) {
  try { await fn(); passed++; console.log("  ok  " + name); }
  catch (e) { failed++; console.log("FAIL  " + name + " — " + e.message); }
}

const ping = await call("ping");
assert.equal(ping.engine, "briefloop-native/1");
console.log(`engine ${ping.engine} · pi ${ping.pi} · ${ping.node}`);

// A reviewer session requires a real model id; deepseek is configured on this
// machine. If auth is absent the create fails with a clear model error, which
// is itself asserted on.
const models = await call("list_models");
const model = models.models.find((m) => m.provider === "deepseek") || models.models[0];
console.log(`test model: ${model.id}`);

let sid;
await check("session_create rejects non-reviewer roles", async () => {
  const msg = await callExpectError("session_create", { session_id: "sx", role: "writer", packet_root: packet, model: model.id });
  assert.match(msg, /reviewer/);
});

await check("reviewer session binds and exposes exactly packet tools", async () => {
  const res = await call("session_create", {
    session_id: "rev1", role: "reviewer", packet_root: packet,
    session_dir: packet, model: model.id, thinking: "high" });
  sid = res.session_id;
  assert.deepEqual(res.tools, ["packet_list", "packet_read"]);
  assert.ok(res.session_file, "session file path reported for audit");
});

await check("packet_list returns packet-relative paths only", async () => {
  const r = await call("tool_call", { session_id: sid, name: "packet_list", args: {} });
  const text = r.content.find((c) => c.type === "text").text;
  assert.match(text, /index\.json/);
  assert.match(text, /sources\/src1\.txt/);
  assert.doesNotMatch(text, /secret/);
});

await check("packet_read returns packet file text", async () => {
  const r = await call("tool_call", { session_id: sid, name: "packet_read", args: { path: "sources/src1.txt" } });
  assert.equal(r.content.find((c) => c.type === "text").text, "evidence text");
});

await check("packet_read honours line ranges", async () => {
  const r = await call("tool_call", { session_id: sid, name: "packet_read", args: { path: "target.json", start_line: 2, end_line: 3 } });
  const text = r.content.find((c) => c.type === "text").text;
  assert.match(text, /lines 2-3 of 6/);
  assert.match(text, /line2\nline3/);
  assert.doesNotMatch(text, /line1|line4/);
});

for (const [name, path] of [
  ["absolute path escape", join(outside, "secret.txt")],
  ["dot-dot escape", "../" + outside.split("/").pop() + "/secret.txt"],
  ["deep dot-dot escape", "../../etc/hosts"],
]) {
  await check("packet_read refuses " + name, async () => {
    const msg = await callExpectError("tool_call", { session_id: sid, name: "packet_read", args: { path } });
    assert.doesNotMatch(msg, /OUTSIDE_SECRET/);
  });
}

await check("packet_read refuses symlink escape inside packet", async () => {
  const msg = await callExpectError("tool_call", { session_id: sid, name: "packet_read", args: { path: "linked-secret.txt" } });
  assert.doesNotMatch(msg, /OUTSIDE_SECRET/);
});

await check("unknown session_id is a clean error", async () => {
  await callExpectError("tool_call", { session_id: "nope", name: "packet_read", args: { path: "index.json" } });
});

await check("tool absent on session is refused", async () => {
  const msg = await callExpectError("tool_call", { session_id: sid, name: "bash", args: {} });
  assert.match(msg, /no such tool/);
});

await check("session_close disposes", async () => {
  await call("session_close", { session_id: sid });
  await callExpectError("tool_call", { session_id: sid, name: "packet_list", args: {} });
});

await call("shutdown");
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
