// Packet-confined tools for the restricted Reviewer.
//
// The reviewer session has NO built-in tools: no read/bash/edit/write, no
// extensions, no context files. Independence comes from what these tools can
// reach, not from how few there are: every tool resolves only inside the frozen
// review packet (checked here, in our code), none writes files, runs programs
// or touches the network. Within that boundary the Reviewer gets the tools the
// job actually needs: locate, read a slice, trace a claim, check arithmetic and
// submit a result that is validated before the run can end.
import { createHash } from "node:crypto";
import { realpathSync, readFileSync, readdirSync, statSync } from "node:fs";
import { extname, isAbsolute, resolve, sep } from "node:path";
import { Type } from "typebox";
import * as JsonSchema from "typebox/schema";
import { defineTool } from "@earendil-works/pi-coding-agent";
type TextContent = { type: "text"; text: string };
type ImageContent = { type: "image"; data: string; mimeType: string };
type Content = TextContent | ImageContent;
type Details = Record<string, unknown>;

// One read returns at most this much text; the model pages with lines or a
// JSON path instead of pulling a whole 250 KB target.json into context.
export const READ_CHARS = 60_000;
const IMAGE_LIMIT = 20 * 1024 * 1024;
const GREP_FILE_LIMIT = 20 * 1024 * 1024;
const LINE_WINDOW = 160;
export const IMAGE_MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};
const BINARY_EXT = new Set([".pdf", ".bin", ".xlsx", ".xls", ".docx", ".doc", ".zip", ".pptx", ...Object.keys(IMAGE_MIME)]);

export function inside(root: string, relative: string): string {
  if (!relative || isAbsolute(relative) || relative.includes("\0")) {
    throw new Error("packet paths are relative to the packet root");
  }
  const resolved = resolve(root, relative);
  const rootWithSep = root.endsWith(sep) ? root : root + sep;
  if (resolved !== root && !resolved.startsWith(rootWithSep)) {
    throw new Error("packet tools only resolve inside the review packet");
  }
  // Every component must be a real file inside the packet; a symlink pointing
  // out of the packet would smuggle in live workspace state.
  const real = realpathSync(resolved);
  if (real !== resolved && !real.startsWith(rootWithSep)) {
    throw new Error("packet tools refuse symlink escapes");
  }
  if (!statSync(real).isFile()) {
    throw new Error(`not a packet file: ${relative}`);
  }
  return real;
}

function walk(dir: string, root: string, out: string[]): void {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) continue; // packets must not contain links (checked again at build)
    const full = resolve(dir, entry.name);
    if (entry.isDirectory()) walk(full, root, out);
    else if (entry.isFile()) out.push(full.slice(root.length + 1).split(sep).join("/"));
  }
}

function text(value: string, details: Details = {}) {
  return { content: [{ type: "text", text: value }] as Content[], details };
}

function clip(value: string, limit = READ_CHARS): string {
  return value.length <= limit ? value : value.slice(0, limit) + `\n[已截断：共 ${value.length} 字符，本次返回前 ${limit} 字符；用 start_line/end_line 或 json_path 读取其余部分]`;
}

// a.b[0].c and a["odd key"] — enough to address any field of a packet JSON.
export function jsonPath(value: unknown, path: string): unknown {
  const tokens: Array<string | number> = [];
  const re = /\s*(?:\.?([A-Za-z_$][\w$-]*)|\[(\d+)\]|\[\s*"((?:[^"\\]|\\.)*)"\s*\])/y;
  let at = 0;
  while (at < path.length) {
    re.lastIndex = at;
    const m = re.exec(path);
    if (!m || m[0].length === 0) throw new Error(`无法解析 json_path：${path}`);
    tokens.push(m[1] ?? (m[2] !== undefined ? Number(m[2]) : JSON.parse(`"${m[3]}"`)));
    at = re.lastIndex;
  }
  let node: any = value;
  const walked: string[] = [];
  for (const token of tokens) {
    if (node === null || typeof node !== "object" || !(token in node)) {
      const keys = node && typeof node === "object" ? (Array.isArray(node) ? `数组长度 ${node.length}` : `可用字段：${Object.keys(node).slice(0, 40).join(", ")}`) : "不是对象或数组";
      throw new Error(`json_path 在 ${walked.join("") || "(根)"} 之后找不到 ${JSON.stringify(token)}；${keys}`);
    }
    node = node[token as any];
    walked.push(typeof token === "number" ? `[${token}]` : `.${token}`);
  }
  return node;
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function window(line: string, index: number, length: number): string {
  if (line.length <= LINE_WINDOW * 2) return line;
  const from = Math.max(0, index - LINE_WINDOW);
  const to = Math.min(line.length, index + length + LINE_WINDOW);
  return (from > 0 ? "…" : "") + line.slice(from, to) + (to < line.length ? "…" : "");
}

// Safe arithmetic: numbers, + - * / ^ %, parentheses and a few functions.
// No identifiers beyond the function names, so nothing can be evaluated but math.
export function calculate(expression: string): number {
  const src = expression.replace(/[,，]/g, "");
  let at = 0;
  const FUNCS: Record<string, (...xs: number[]) => number> = {
    abs: Math.abs, sqrt: Math.sqrt, ln: Math.log, log10: Math.log10, exp: Math.exp,
    min: Math.min, max: Math.max, pow: Math.pow,
    round: (x: number, digits = 0) => { const f = 10 ** digits; return Math.round(x * f) / f; },
  };
  const peek = () => { while (src[at] === " ") at++; return src[at]; };
  const expect = (ch: string) => { if (peek() !== ch) throw new Error(`第 ${at + 1} 个字符处应为 ${ch}`); at++; };
  const primary = (): number => {
    const ch = peek();
    if (ch === "(") { at++; const v = sum(); expect(")"); return v; }
    if (ch === "-") { at++; return -power(); }
    if (ch === "+") { at++; return power(); }
    const num = /^\d+(?:\.\d+)?(?:[eE][-+]?\d+)?/.exec(src.slice(at));
    if (num) { at += num[0].length; return Number(num[0]); }
    const name = /^[a-z][a-z0-9]*/.exec(src.slice(at));
    if (name && FUNCS[name[0]]) {
      at += name[0].length; expect("(");
      const args = [sum()];
      while (peek() === ";") { at++; args.push(sum()); }
      expect(")");
      return FUNCS[name[0]](...args);
    }
    throw new Error(`第 ${at + 1} 个字符无法识别：${src.slice(at, at + 12) || "(表达式结束)"}`);
  };
  const percent = (): number => { let v = primary(); while (peek() === "%") { at++; v = v / 100; } return v; };
  const power = (): number => { const base = percent(); if (peek() === "^") { at++; return base ** power(); } return base; };
  const product = (): number => {
    let v = power();
    for (;;) {
      const op = peek();
      if (op === "*") { at++; v *= power(); }
      else if (op === "/") { at++; v /= power(); }
      else return v;
    }
  };
  const sum = (): number => {
    let v = product();
    for (;;) {
      const op = peek();
      if (op === "+") { at++; v += product(); }
      else if (op === "-") { at++; v -= product(); }
      else return v;
    }
  };
  const value = sum();
  if (peek() !== undefined) throw new Error(`第 ${at + 1} 个字符处有多余内容：${src.slice(at, at + 12)}`);
  if (!Number.isFinite(value)) throw new Error("结果不是有限数值（除以零或溢出）");
  return value;
}

// Strip hashes and bound long strings so a trace stays readable.
function compact(value: unknown, limit = 1500): unknown {
  if (typeof value === "string") return value.length > limit ? value.slice(0, limit) + "…" : value;
  if (Array.isArray(value)) return value.map((item) => compact(item, limit));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      if (key === "hash" || key.endsWith("_hash") || key === "created") continue;
      out[key] = compact(item, limit);
    }
    return out;
  }
  return value;
}

function blockText(node: any): string {
  if (!node || typeof node !== "object") return "";
  if (node.type === "text" && typeof node.text === "string") return node.text;
  if (node.type === "citation") return `[${node.attrs?.sourceId ?? "引用"}]`;
  return Array.isArray(node.content) ? node.content.map(blockText).join("") : "";
}

function findBlock(node: any, id: string): any {
  if (!node || typeof node !== "object") return undefined;
  if (node.attrs?.blockId === id) return node;
  for (const child of Array.isArray(node.content) ? node.content : []) {
    const found = findBlock(child, id);
    if (found) return found;
  }
  return undefined;
}

export interface SubmitHooks {
  // Resolves with an error message when the runner rejects the result.
  admit(review: Record<string, unknown>): Promise<string | undefined>;
  accept(review: Record<string, unknown>): void;
}

// One line per tool for the system prompt: pi's customPrompt branch does not
// carry tool snippets, so the engine states the real toolset itself.
export const TOOL_GUIDE: Record<string, string> = {
  packet_list: "列出核查包内全部文件及大小。",
  packet_read: "读取包内文件。核对来源时读完整份或完整相关部分，在一份材料里核对它支撑的全部内容；超长文件用 start_line/end_line 分段，大 JSON 用 json_path 取字段；图片文件返回图像（模型不接收图像时只返回说明）。要读几份就把其余放进 more；单处最多约 6 万字符。",
  packet_grep: "在包内文本文件中查找关键词或正则，返回文件、行号、命中片段及前后各 1 行，用来找出内容在哪份文件、哪个位置；可用 patterns 一次查多个词。定位后读取相关来源再核对，不要逐个数字搜索。",
  claim_trace: "按 claim_id 一次取回主张内容、支持说明、绑定证据片段、所在正文段落和前提链。",
  calc: "对正文数字做确定性计算：四则运算、^、%、abs/round/min/max/sqrt/ln/log10/exp/pow（多个参数用分号分隔）。用于核对增长率、占比、加总和单位换算，不要心算。",
  submit_review: "提交最终审阅结果。先用 review 提交完整对象；提交时按 output.schema.json 和本次允许的 ID 当场校验，未通过时只用 patch 重交需要修改的顶层字段，会与上次草稿合并。通过即结束本次审阅，不要再在回复正文里输出 JSON。",
};

export function toolGuide(names: string[]): string {
  return ["## 本次可用工具", "一次回复可以同时调用多个工具；互不依赖的调用会并行执行，submit_review 单独提交。",
    ...names.map((name) => `- ${name}：${TOOL_GUIDE[name] ?? ""}`)].join("\n");
}

// runnerAdmits: the runner's admission (the validator that really admits a
// result) checks structure too. The exported JSON Schema is stricter than it
// (no field aliases), so checking it first rejected results the runner would
// accept and cost the model a whole extra round.
export function packetTools(packetRoot: string, hooks?: SubmitHooks, acceptsImages: () => boolean = () => true, runnerAdmits = false) {
  const root = realpathSync(packetRoot);
  let target: any;
  const loadTarget = () => (target ??= JSON.parse(readFileSync(inside(root, "target.json"), "utf-8")));

  const packetList = defineTool({
    name: "packet_list",
    label: "列出核查包文件",
    description: "列出本次固定核查包中的全部文件（相对核查包根目录的路径与字节数）。核查包是唯一可读的资料。",
    parameters: Type.Object({}),
    execute: async () => {
      const files: string[] = [];
      walk(root, root, files);
      files.sort();
      const lines = files.map((file) => `${file}\t${statSync(resolve(root, file)).size}`);
      return text(lines.join("\n"), { count: files.length });
    },
  });

  type ReadSpec = { path: string; start_line?: number; end_line?: number; json_path?: string };
  const readOne = (spec: ReadSpec): { content: Content[]; details: Details } => {
    const file = inside(root, spec.path);
    const ext = extname(file).toLowerCase();
    const mime = IMAGE_MIME[ext];
    if (mime) {
      const bytes = readFileSync(file);
      if (!acceptsImages()) {
        return text(`${spec.path} 是图片（${bytes.length} 字节），当前模型不接收图像输入，无法目视核验；可读取同目录的图表数据文件核对数值，并把目视核验列为未核验事项。`, { image: false, bytes: bytes.length });
      }
      if (bytes.length > IMAGE_LIMIT) throw new Error(`图片超过 ${IMAGE_LIMIT} 字节`);
      const image: ImageContent = { type: "image", data: bytes.toString("base64"), mimeType: mime };
      return { content: [{ type: "text", text: `${spec.path}（${bytes.length} 字节）` }, image], details: { image: true, bytes: bytes.length } };
    }
    if (BINARY_EXT.has(ext)) {
      return text(`${spec.path} 是二进制原件，不能按文本读取；请读取同名来源的 .txt 或 .view.json。`, { binary: true });
    }
    const raw = readFileSync(file, "utf-8");
    if (spec.json_path) {
      const value = jsonPath(JSON.parse(raw), spec.json_path);
      const body = JSON.stringify(value, null, 1);
      return text(clip(body), { json_path: spec.json_path, chars: body.length });
    }
    const lines = raw.split("\n");
    if (spec.start_line !== undefined || spec.end_line !== undefined) {
      const from = Math.max(1, Math.floor(spec.start_line ?? 1));
      const to = Math.min(lines.length, Math.floor(spec.end_line ?? lines.length));
      const body = `[第 ${from}-${to} 行，共 ${lines.length} 行]\n` + lines.slice(from - 1, to).join("\n");
      return text(clip(body), { lines: lines.length, from, to });
    }
    return text(clip(raw), { lines: lines.length, chars: raw.length });
  };
  const readParams = {
    path: Type.String({ description: "核查包内的相对路径" }),
    start_line: Type.Optional(Type.Number({ description: "起始行（从 1 开始）" })),
    end_line: Type.Optional(Type.Number({ description: "结束行（含）" })),
    json_path: Type.Optional(Type.String({ description: "JSON 字段路径，如 requirements.requirement_items 或 evidence.bindings[2].claim" })),
  };

  const packetRead = defineTool({
    name: "packet_read",
    label: "读取核查包文件",
    description:
      "读取核查包内的文件。path 相对核查包根目录（如 target.json、sources/<id>.view.json、history/responses.json）。" +
      "长文本用 start_line/end_line 读取一段；JSON 文件可用 json_path 只取某个字段；图片文件返回图像内容。" +
      "要同时读几处时，把其余各处放进 more，一次调用全部返回。",
    parameters: Type.Object({
      ...readParams,
      more: Type.Optional(Type.Array(Type.Object(readParams), { description: "同时读取的其他片段，最多 8 处", maxItems: 8 })),
    }),
    execute: async (_id, params) => {
      const specs: ReadSpec[] = [params, ...(params.more ?? [])].slice(0, 9);
      if (specs.length === 1) return readOne(specs[0]);
      // Each piece is read on its own, so one bad path does not lose the rest.
      const content: Content[] = [];
      let used = 0;
      for (const spec of specs) {
        const label = `=== ${spec.path}${spec.json_path ? " @" + spec.json_path : ""}${spec.start_line !== undefined || spec.end_line !== undefined ? ` 第 ${spec.start_line ?? 1}-${spec.end_line ?? "末"} 行` : ""} ===`;
        let piece: Content[];
        try { piece = readOne(spec).content; }
        catch (err) { piece = [{ type: "text", text: `读取失败：${err instanceof Error ? err.message : String(err)}` }]; }
        for (const item of piece) {
          if (item.type === "text") {
            const room = Math.max(0, READ_CHARS * 2 - used);
            const body = item.text.length > room ? item.text.slice(0, room) + "\n[本次合并读取已达上限，其余片段请另行读取]" : item.text;
            used += body.length;
            content.push({ type: "text", text: `${label}\n${body}` });
          } else content.push(item);
        }
      }
      return { content, details: { pieces: specs.length, chars: used } as Details };
    },
  });

  const packetGrep = defineTool({
    name: "packet_grep",
    label: "搜索核查包",
    description:
      "在核查包的文本文件中搜索关键词（默认按字面匹配）或正则，返回“文件:行号: 命中片段”，默认附带前后各 1 行。" +
      "要查多个词时放进 patterns，一次调用分别返回。可用 path 限定文件或目录前缀（如 sources/ 或 target.json）。",
    parameters: Type.Object({
      pattern: Type.Optional(Type.String({ description: "要查找的文字；regex=true 时为 JavaScript 正则" })),
      patterns: Type.Optional(Type.Array(Type.String(), { description: "同时查找的多个词，最多 12 个", maxItems: 12 })),
      regex: Type.Optional(Type.Boolean({ description: "按正则匹配，默认 false" })),
      path: Type.Optional(Type.String({ description: "只搜索该文件或以此开头的路径" })),
      ignore_case: Type.Optional(Type.Boolean({ description: "忽略大小写，默认 true" })),
      context: Type.Optional(Type.Number({ description: "每处命中前后附带的行数，默认 1，最多 5" })),
      max_matches: Type.Optional(Type.Number({ description: "每个词最多返回的命中数，默认 20，最多 200" })),
    }),
    execute: async (_id, params) => {
      const wanted = [...(params.pattern ? [params.pattern] : []), ...(params.patterns ?? [])].filter(Boolean).slice(0, 12);
      if (wanted.length === 0) throw new Error("pattern 或 patterns 至少给一个");
      const flags = params.ignore_case === false ? "" : "i";
      const limit = Math.max(1, Math.min(200, Math.floor(params.max_matches ?? 20)));
      const context = Math.max(0, Math.min(5, Math.floor(params.context ?? 1)));
      const prefix = (params.path ?? "").replace(/^\.\//, "");
      if (prefix && (isAbsolute(prefix) || prefix.split("/").includes(".."))) throw new Error("path 必须是核查包内的相对路径");
      const files: string[] = [];
      walk(root, root, files);
      files.sort();
      const texts: Array<[string, string[]]> = [];
      for (const file of files) {
        if (prefix && file !== prefix && !file.startsWith(prefix)) continue;
        if (BINARY_EXT.has(extname(file).toLowerCase())) continue;
        const full = inside(root, file);
        if (statSync(full).size > GREP_FILE_LIMIT) continue;
        texts.push([file, readFileSync(full, "utf-8").split("\n")]);
      }
      const sections: string[] = [];
      let totalAll = 0;
      for (const pattern of wanted) {
        const re = new RegExp(params.regex ? pattern : escapeRegex(pattern), flags);
        const out: string[] = [];
        let total = 0;
        for (const [file, lines] of texts) {
          for (let i = 0; i < lines.length; i++) {
            const m = re.exec(lines[i]);
            if (!m) continue;
            total += 1;
            if (out.length >= limit) continue;
            const block: string[] = [];
            for (let j = Math.max(0, i - context); j < i; j++) block.push(`${file}-${j + 1}- ${window(lines[j], 0, 0)}`);
            block.push(`${file}:${i + 1}: ${window(lines[i], m.index, m[0].length)}`);
            for (let j = i + 1; j <= Math.min(lines.length - 1, i + context); j++) block.push(`${file}-${j + 1}- ${window(lines[j], 0, 0)}`);
            out.push(block.join("\n"));
          }
        }
        totalAll += total;
        const head = total === 0 ? "没有命中。" : `共 ${total} 处命中${total > out.length ? `，显示前 ${out.length} 处；缩小 path 或换更具体的词` : ""}。`;
        sections.push((wanted.length > 1 ? `=== ${pattern} ===\n` : "") + [head, ...out].join("\n"));
      }
      return text(clip(sections.join("\n\n"), READ_CHARS * 2), { matches: totalAll, patterns: wanted.length });
    },
  });

  const claimTrace = defineTool({
    name: "claim_trace",
    label: "追溯主张",
    description: "按 claim_id 返回主张内容、支持说明、绑定的证据片段、所在正文段落与前提主张，数据取自 target.json。",
    parameters: Type.Object({ claim_id: Type.String({ description: "target.json 中的 claim_id" }) }),
    execute: async (_id, params) => {
      const current = loadTarget();
      const bindings: any[] = current?.evidence?.bindings ?? [];
      const candidates: any[] = current?.candidate_claims ?? [];
      const seen: Array<{ node: any; origin: string; parent?: string }> = [];
      const visit = (node: any, origin: string, parent?: string) => {
        if (!node || typeof node !== "object") return;
        if (node.claim_id === params.claim_id) seen.push({ node, origin, parent });
        for (const premise of node.premises ?? []) visit(premise, origin, node.claim_id);
      };
      for (const binding of bindings) visit(binding, "正文绑定");
      for (const candidate of candidates) visit(candidate, "候选主张（未用于正文）");
      if (seen.length === 0) {
        const known = [...bindings, ...candidates].map((b) => b.claim_id).filter(Boolean);
        throw new Error(`target.json 中没有 ${params.claim_id}；已登记的顶层主张：${known.slice(0, 60).join(", ")}`);
      }
      const result = seen.map(({ node, origin, parent }) => {
        const block = node.block_id ? findBlock(current.document, node.block_id) : undefined;
        return {
          origin,
          ...(parent ? { premise_of: parent } : {}),
          claim_id: node.claim_id,
          block_id: node.block_id,
          block_text: block ? compact(blockText(block), 3000) : undefined,
          claim: compact(node.claim?.data ?? node.claim),
          evidence: compact((node.evidence ?? []).map((item: any) => ({ span_id: item.id, ...(item.data ?? item) }))),
          premises: (node.premises ?? []).map((premise: any) => premise.claim_id),
        };
      });
      return text(clip(JSON.stringify(result, null, 1)), { occurrences: result.length });
    },
  });

  const calc = defineTool({
    name: "calc",
    label: "计算",
    description: "计算一个算术表达式。支持 + - * / ^ %、括号，以及 abs、round(x; 位数)、min、max、sqrt、ln、log10、exp、pow；函数的多个参数用分号分隔，数字中的千分位逗号会被忽略。",
    parameters: Type.Object({
      expression: Type.String({ description: "如 (58.9-85.9)/85.9*100 或 round(261.0/139.1-1; 4)" }),
    }),
    execute: async (_id, params) => {
      const value = calculate(params.expression);
      return text(`${params.expression} = ${Number(value.toPrecision(12))}`, { value });
    },
  });

  let validator: ReturnType<typeof JsonSchema.Compile> | undefined;
  // The last submitted draft, kept so a rejected result can be fixed by
  // resending only the fields that failed instead of the whole object.
  let draft: Record<string, unknown> | undefined;
  const submitReview = defineTool({
    name: "submit_review",
    label: "提交审阅结果",
    description:
      "提交最终审阅结果。第一次用 review 提交完整结果对象（结构见 output.schema.json）。提交时校验结构与本次允许的 ID；" +
      "未通过时只用 patch 重新提交需要修改的顶层字段（如 {\"requirement_checks\": [...]}），它会与上次提交的草稿合并后再校验，不要重写整个对象。",
    parameters: Type.Object({
      review: Type.Optional(Type.Object({}, { additionalProperties: true, description: "完整审阅结果对象" })),
      patch: Type.Optional(Type.Object({}, { additionalProperties: true, description: "只含需要替换的顶层字段，与上次提交合并" })),
    }),
    // Submission settles the run; it must never race another call in the batch.
    executionMode: "sequential",
    execute: async (_id, params) => {
      const parse = (value: unknown, name: string): Record<string, unknown> | undefined => {
        if (value === undefined) return undefined;
        if (typeof value === "string") {
          try { value = JSON.parse(value); } catch { throw new Error(`${name} 必须是 JSON 对象，而不是无法解析的字符串`); }
        }
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${name} 必须是 JSON 对象`);
        return value as Record<string, unknown>;
      };
      const full = parse(params.review, "review");
      const patch = parse(params.patch, "patch");
      if (!full && !patch) throw new Error("需要 review（完整结果）或 patch（修改的字段）");
      if (!full && !draft) throw new Error("还没有提交过完整结果，请先用 review 提交完整对象");
      const review = full ?? { ...draft!, ...patch };
      draft = review;
      validator ??= JsonSchema.Compile(JSON.parse(readFileSync(inside(root, "output.schema.json"), "utf-8")));
      const [ok, errors] = runnerAdmits && hooks ? [true, []] as [boolean, never[]]
        : validator.Errors(review) as unknown as [boolean, Array<{ instancePath: string; message: string; params?: unknown }>];
      if (!ok) {
        const lines = errors.slice(0, 30).map((e) => `${e.instancePath || "(根)"}：${e.message}${e.params ? " " + JSON.stringify(e.params) : ""}`);
        throw new Error(`结构校验未通过（${errors.length} 处），只修改这些字段后重新提交：\n${lines.join("\n")}`);
      }
      const rejected = hooks ? await hooks.admit(review as Record<string, unknown>) : undefined;
      if (rejected) throw new Error(`接纳检查未通过，修正后重新提交：\n${rejected}`);
      hooks?.accept(review as Record<string, unknown>);
      const digest = createHash("sha256").update(JSON.stringify(review)).digest("hex").slice(0, 16);
      return { content: [{ type: "text", text: "审阅结果已通过校验并提交，本次审阅结束。" }] as Content[], details: { accepted: true, digest } as Details, terminate: true };
    },
  });

  return [packetList, packetRead, packetGrep, claimTrace, calc, submitReview];
}
