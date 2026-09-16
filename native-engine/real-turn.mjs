// One real model turn through the wire protocol against a fixture packet.
// Usage: MODEL=openrouter/<m> node real-turn.mjs   (provider key via env)
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BUNDLE = new URL("../src/briefloop/static/native-engine.mjs", import.meta.url).pathname;
const MODEL = process.env.MODEL || "openrouter/deepseek/deepseek-chat-v3.1";
const THINKING = process.env.THINKING || "low";

const packet = mkdtempSync(join(tmpdir(), "bl-real-packet-"));
mkdirSync(join(packet, "sources"));
writeFileSync(join(packet, "index.json"), JSON.stringify({ fingerprint: "fp7", version_id: "brief_t1" }));
writeFileSync(join(packet, "target.json"), JSON.stringify(
  { blocks: [{ id: "b1", text: "Paris is the capital of France.", citations: ["s1"] }] }, null, 2));
writeFileSync(join(packet, "sources", "s1.txt"), "Paris is the capital and most populous city of France.");

const proc = spawn(process.env.BRIEFLOOP_NODE || "node", [BUNDLE], { stdio: ["pipe", "pipe", "inherit"], env: process.env });
let buf = "", text = "";
const t0 = Date.now();
const tools = [];
proc.stdout.on("data", (d) => {
  buf += d;
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const m = JSON.parse(buf.slice(0, i)); buf = buf.slice(i + 1);
    if (m.method === "event") {
      const e = m.params;
      if (e.kind === "tool") { tools.push(e); console.log(((Date.now()-t0)/1000).toFixed(1) + "s tool", e.name, e.status, JSON.stringify(e.input || {}).slice(0, 90)); }
      if (e.kind === "text") text += e.delta;
      if (e.kind === "error") console.log("ERR EV", String(e.message).slice(0, 200));
      if (e.kind === "usage") console.log("usage", JSON.stringify(e.usage).slice(0, 160));
      if (e.kind === "end") {
        console.log("END", e.status, ((Date.now()-t0)/1000).toFixed(1) + "s", "session:", e.session_file);
        console.log("---TEXT---"); console.log(text.slice(0, 2000));
        console.log("tool_calls:", tools.length);
        proc.kill(); process.exit(0);
      }
    } else console.log("RES", JSON.stringify(m.result || m.error).slice(0, 300));
  }
});
const send = (id, method, params) => proc.stdin.write(JSON.stringify({ id, method, params }) + "\n");
send("1", "session_create", { session_id: "rev", role: "reviewer", packet_root: packet,
  session_dir: packet + "-sess", model: MODEL, thinking: THINKING,
  max_tokens: Number(process.env.MAX_TOKENS || 0) || undefined });
setTimeout(() => send("2", "turn_start", { session_id: "rev", execution_id: "exec",
  prompt: "用 packet_list 列出核查包文件，用 packet_read 读 target.json 和 sources/s1.txt，然后只回复一个 JSON：{\"checked\":true,\"supported\":true/false}，表示 b1 断言是否有 s1 依据。" }), 5000);
setTimeout(() => { console.log("TIMEOUT"); proc.kill(); process.exit(1); }, 240000);
