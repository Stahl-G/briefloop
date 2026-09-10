// Includes Apache-2.0 Open Design helpers; see runtime-bridge.LICENSE.txt and runtime-bridge.NOTICE.txt.

// runtime-bridge/main.ts
import { spawn as spawn2, execFile } from "node:child_process";
import { accessSync, constants, readFileSync } from "node:fs";
import { homedir as homedir2 } from "node:os";
import path2 from "node:path";
import { promisify } from "node:util";
import { createInterface } from "node:readline";

// third_party/open-design/core/json-line-stream.ts
function createJsonLineStream(onMessage) {
  let buffer = "";
  let pendingJsonLines = [];
  const emit2 = (candidate) => {
    try {
      onMessage(JSON.parse(candidate), candidate);
      return true;
    } catch {
      return false;
    }
  };
  const pendingCandidate = () => pendingJsonLines.join("\n");
  const startPendingJson = (line) => {
    pendingJsonLines = [line];
  };
  const resetPendingJson = () => {
    pendingJsonLines = [];
  };
  const replayPendingJsonLines = () => {
    const absorbed = pendingJsonLines;
    pendingJsonLines = [];
    for (const line of absorbed) {
      emit2(line);
    }
  };
  const handleLine = (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    if (pendingJsonLines.length > 0) {
      const nextCandidate = `${pendingCandidate()}
${trimmed}`;
      if (emit2(nextCandidate)) {
        resetPendingJson();
        return;
      }
      const state = classifyJsonCandidate(nextCandidate);
      if (state === "incomplete" && nextCandidate.length <= 128e3 && pendingJsonLines.length < 256) {
        pendingJsonLines.push(trimmed);
        return;
      }
      replayPendingJsonLines();
      handleLine(trimmed);
      return;
    }
    if (emit2(trimmed)) return;
    if ((trimmed.startsWith("{") || trimmed.startsWith("[")) && classifyJsonCandidate(trimmed) === "incomplete") {
      startPendingJson(trimmed);
    }
  };
  return {
    feed(chunk) {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        handleLine(line);
      }
    },
    flush() {
      const trimmed = buffer.trim();
      buffer = "";
      if (trimmed) {
        handleLine(trimmed);
      }
      if (pendingJsonLines.length > 0) {
        if (emit2(pendingCandidate())) {
          resetPendingJson();
        } else {
          replayPendingJsonLines();
        }
      }
    }
  };
}
function classifyJsonCandidate(value) {
  const stack = [];
  let rootComplete = false;
  const afterValue = () => {
    const parent = stack.at(-1);
    if (!parent) {
      rootComplete = true;
      return;
    }
    parent.expect = "commaOrEnd";
  };
  const closeFrame = (kind) => {
    const current = stack.pop();
    if (!current || current.kind !== kind) return false;
    afterValue();
    return true;
  };
  const parseString = (start) => {
    for (let index = start + 1; index < value.length; index += 1) {
      const char = value[index];
      if (char === "\\") {
        index += 1;
        continue;
      }
      if (char === '"') return index;
    }
    return null;
  };
  const parseLiteral = (start, literal) => {
    for (let offset = 0; offset < literal.length; offset += 1) {
      const char = value[start + offset];
      if (char === void 0) return null;
      if (char !== literal[offset]) return false;
    }
    return start + literal.length - 1;
  };
  const parseNumber = (start) => {
    let index = start;
    if (value[index] === "-") index += 1;
    if (value[index] === "0") {
      index += 1;
    } else if (/[1-9]/.test(value[index] ?? "")) {
      while (/[0-9]/.test(value[index] ?? "")) index += 1;
    } else {
      return false;
    }
    if (value[index] === ".") {
      index += 1;
      if (!/[0-9]/.test(value[index] ?? "")) return false;
      while (/[0-9]/.test(value[index] ?? "")) index += 1;
    }
    if (value[index] === "e" || value[index] === "E") {
      index += 1;
      if (value[index] === "+" || value[index] === "-") index += 1;
      if (!/[0-9]/.test(value[index] ?? "")) return false;
      while (/[0-9]/.test(value[index] ?? "")) index += 1;
    }
    return index - 1;
  };
  const parseValue = (index) => {
    const char = value[index];
    if (char === '"') {
      const end = parseString(index);
      if (end === null) return null;
      afterValue();
      return end;
    }
    if (char === "{") {
      stack.push({ kind: "object", expect: "keyOrEnd" });
      return index;
    }
    if (char === "[") {
      stack.push({ kind: "array", expect: "valueOrEnd" });
      return index;
    }
    if (char === "t") {
      const end = parseLiteral(index, "true");
      if (end === false || end === null) return end;
      afterValue();
      return end;
    }
    if (char === "f") {
      const end = parseLiteral(index, "false");
      if (end === false || end === null) return end;
      afterValue();
      return end;
    }
    if (char === "n") {
      const end = parseLiteral(index, "null");
      if (end === false || end === null) return end;
      afterValue();
      return end;
    }
    if (char === "-" || /[0-9]/.test(char ?? "")) {
      const end = parseNumber(index);
      if (end === false) return false;
      afterValue();
      return end;
    }
    return false;
  };
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (char === void 0) break;
    if (/\s/.test(char)) continue;
    const current = stack.at(-1);
    if (!current) {
      if (rootComplete) return "invalid";
      const end = parseValue(index);
      if (end === false) return "invalid";
      if (end === null) return "incomplete";
      index = end;
      continue;
    }
    if (current.kind === "object") {
      if (current.expect === "keyOrEnd") {
        if (char === "}") {
          if (!closeFrame("object")) return "invalid";
          continue;
        }
        if (char !== '"') return "invalid";
        const end = parseString(index);
        if (end === null) return "incomplete";
        current.expect = "colon";
        index = end;
        continue;
      }
      if (current.expect === "colon") {
        if (char !== ":") return "invalid";
        current.expect = "value";
        continue;
      }
      if (current.expect === "value") {
        const end = parseValue(index);
        if (end === false) return "invalid";
        if (end === null) return "incomplete";
        index = end;
        continue;
      }
      if (char === "}") {
        if (!closeFrame("object")) return "invalid";
        continue;
      }
      if (char !== ",") return "invalid";
      current.expect = "keyOrEnd";
      continue;
    }
    if (current.expect === "valueOrEnd") {
      if (char === "]") {
        if (!closeFrame("array")) return "invalid";
        continue;
      }
      const end = parseValue(index);
      if (end === false) return "invalid";
      if (end === null) return "incomplete";
      index = end;
      continue;
    }
    if (char === "]") {
      if (!closeFrame("array")) return "invalid";
      continue;
    }
    if (char !== ",") return "invalid";
    current.expect = "valueOrEnd";
  }
  return rootComplete && stack.length === 0 ? "complete" : "incomplete";
}

// third_party/open-design/acp/session-params.ts
import path from "node:path";
function buildAcpSessionNewParams(cwd, { mcpServers, envFormat = "array" } = {}) {
  const servers = Array.isArray(mcpServers) ? mcpServers : [];
  const wantsMap = envFormat === "map";
  return {
    cwd: path.resolve(cwd),
    // MCP is an optional compatibility layer. Default to no MCP servers so ACP
    // agents can run through the skill + CLI path without MCP support. Do not
    // auto-install or mutate user/global MCP config; callers must pass an
    // explicit per-session MCP descriptor when a compatible agent supports it.
    mcpServers: servers.map((s) => {
      const rawEnv = s?.env;
      const isPlainObject = rawEnv && typeof rawEnv === "object" && !Array.isArray(rawEnv);
      if (wantsMap && isPlainObject) {
        return {
          type: typeof s?.type === "string" ? s.type : "stdio",
          name: typeof s?.name === "string" ? s.name : "",
          command: typeof s?.command === "string" ? s.command : "",
          args: Array.isArray(s?.args) ? s.args : [],
          env: rawEnv
        };
      }
      const envArr = Array.isArray(rawEnv) ? rawEnv : [];
      const env2 = wantsMap ? Object.fromEntries(envArr.map((e) => [e?.name ?? "", e?.value ?? ""])) : isPlainObject ? Object.entries(rawEnv).map(
        ([name, value]) => ({ name, value })
      ) : envArr;
      return {
        type: typeof s?.type === "string" ? s.type : "stdio",
        name: typeof s?.name === "string" ? s.name : "",
        command: typeof s?.command === "string" ? s.command : "",
        args: Array.isArray(s?.args) ? s.args : [],
        env: env2
      };
    })
  };
}
function buildPromptBlocks(prompt, resourcePaths) {
  const blocks = [{ type: "text", text: prompt }];
  for (const resourcePath of resourcePaths) {
    if (typeof resourcePath !== "string" || resourcePath.trim().length === 0) continue;
    blocks.push({ type: "resource_link", uri: resourcePath });
  }
  return blocks;
}

// third_party/open-design/acp/models.ts
import { spawn } from "node:child_process";

// third_party/open-design/acp/constants.ts
var ACP_PROTOCOL_VERSION = 1;
var DEFAULT_TIMEOUT_MS = 15e3;
var MAX_TIMEOUT_MS = 24 * 60 * 60 * 1e3;
var DEFAULT_STAGE_TIMEOUT_MS = 10 * 60 * 1e3;
var ACP_ARTIFACT_OPEN_PATTERN = String.raw`<\s*(?:\|?\s*DSML[\s,]+artifact\b|artifact\b)`;
var ACP_GENERATED_FILE_PREFIX_PATTERN = String.raw`(?:here\s+is|here'?s)\s+the\s+generated\s+file\s*:?\s*(?:\r?\n|\s)*`;
var ACP_ARTIFACT_ECHO_START_RE = new RegExp(
  String.raw`^\s*(?:${ACP_ARTIFACT_OPEN_PATTERN}|${ACP_GENERATED_FILE_PREFIX_PATTERN}${ACP_ARTIFACT_OPEN_PATTERN})`,
  "i"
);
var MODEL_CONFIG_OPTION_IDS = /* @__PURE__ */ new Set(["model", "models", "modelid", "modelids"]);

// third_party/open-design/acp/json.ts
function errorMessage(err) {
  return err instanceof Error ? err.message : String(err);
}
function resolveAcpTimeoutMs(env2, fallbackMs) {
  const raw = Number(env2.OD_ACP_TIMEOUT_MS);
  if (!Number.isFinite(raw)) return fallbackMs;
  return Math.min(MAX_TIMEOUT_MS, Math.max(0, Math.floor(raw)));
}
function asObject(value) {
  return value && typeof value === "object" ? value : null;
}

// third_party/open-design/acp/rpc.ts
function sendRpc(writable, id, method, params, observeSerializedFrame) {
  const frame = `${JSON.stringify({ jsonrpc: "2.0", id, method, params })}
`;
  writable.write(frame);
  try {
    observeSerializedFrame?.({
      method,
      frameBytes: Buffer.byteLength(frame, "utf8")
    });
  } catch {
  }
}
function rpcErrorMessage(raw) {
  const obj = asObject(raw);
  const error = asObject(obj?.error);
  if (!obj || !error) {
    return "";
  }
  const message = typeof error.message === "string" ? error.message : typeof error.code === "number" ? String(error.code) : "json-rpc error";
  return typeof obj.id === "number" ? `json-rpc id ${obj.id}: ${message}` : message;
}

// third_party/open-design/acp/models.ts
function normalizeConfigOptionToken(value) {
  return typeof value === "string" ? value.trim().toLowerCase().replace(/[\s_-]+/g, "") : "";
}
function isModelConfigOption(option, configId) {
  const category = normalizeConfigOptionToken(option.category);
  if (category === "model") return true;
  const id = normalizeConfigOptionToken(configId);
  if (id === "model") return true;
  if (category) return false;
  const name = normalizeConfigOptionToken(option.name);
  return MODEL_CONFIG_OPTION_IDS.has(id) || name === "model";
}
function findModelConfigOption(configOptions) {
  const options = Array.isArray(configOptions) ? configOptions : [];
  for (const rawOption of options) {
    const option = asObject(rawOption);
    if (!option) continue;
    const configId = typeof option.id === "string" ? option.id.trim() : "";
    if (!configId) continue;
    const type = typeof option.type === "string" ? option.type.trim() : "";
    if (type && type !== "select") continue;
    if (!isModelConfigOption(option, configId)) continue;
    const currentValue = typeof option.currentValue === "string" && option.currentValue.trim() ? option.currentValue.trim() : null;
    return {
      configId,
      currentValue,
      values: Array.isArray(option.options) ? option.options : []
    };
  }
  return null;
}
function normalizeModelConfigOptions(configOptions, defaultModelOption) {
  const modelConfig = findModelConfigOption(configOptions);
  if (!modelConfig) return null;
  const seen = /* @__PURE__ */ new Set([defaultModelOption.id]);
  const out = [defaultModelOption];
  for (const rawValue of modelConfig.values) {
    const value = asObject(rawValue);
    if (!value) continue;
    const id = typeof value.value === "string" && value.value.trim() ? value.value.trim() : typeof value.id === "string" ? value.id.trim() : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const name = typeof value.name === "string" ? value.name.trim() : "";
    const isCurrent = id === modelConfig.currentValue;
    const labelBase = name && name !== id ? `${name} (${id})` : id;
    out.push({ id, label: isCurrent ? `${labelBase} \u2022 current` : labelBase });
  }
  return { currentModelId: modelConfig.currentValue, models: out };
}
function normalizeModels(models, defaultModelOption, configOptions) {
  const configModels = normalizeModelConfigOptions(configOptions, defaultModelOption);
  if (configModels && configModels.models.length > 1) {
    return configModels.models;
  }
  const modelsObj = asObject(models);
  const available = Array.isArray(modelsObj?.availableModels) ? modelsObj.availableModels : [];
  const currentModelId = typeof modelsObj?.currentModelId === "string" ? modelsObj.currentModelId : null;
  const seen = /* @__PURE__ */ new Set([defaultModelOption.id]);
  const out = [defaultModelOption];
  for (const model of available) {
    const id = typeof model?.modelId === "string" ? model.modelId.trim() : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const name = typeof model?.name === "string" ? model.name.trim() : "";
    const isCurrent = id === currentModelId;
    const labelBase = name && name !== id ? `${name} (${id})` : id;
    out.push({ id, label: isCurrent ? `${labelBase} \u2022 current` : labelBase });
  }
  return out.length > 1 || !configModels ? out : configModels.models;
}
async function detectAcpModels({
  bin,
  args,
  cwd = process.cwd(),
  env: env2 = process.env,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  clientName = "open-design-detect",
  clientVersion = "runtime-adapter",
  defaultModelOption = { id: "default", label: "Default (CLI config)" }
}) {
  const effectiveTimeoutMs = resolveAcpTimeoutMs(env2, timeoutMs);
  return await new Promise((resolve, reject) => {
    const child = spawn(bin, args, {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...env2 }
    });
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    let settled = false;
    let stderrBuf = "";
    let expectedId = 1;
    let nextId = 2;
    let timer = null;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      try {
        child.stdin.end();
      } catch {
      }
      fn(value);
    };
    const fail = (message) => {
      finish(reject, new Error(message));
      if (!child.killed) child.kill("SIGTERM");
    };
    const writeRpc = (id, method, params) => {
      try {
        sendRpc(child.stdin, id, method, params);
      } catch (err) {
        fail(`stdin write failed: ${errorMessage(err)}`);
      }
    };
    const sendSessionNew = () => {
      expectedId = nextId;
      writeRpc(nextId, "session/new", buildAcpSessionNewParams(cwd));
      nextId += 1;
    };
    const parser = createJsonLineStream((raw) => {
      const obj = asObject(raw);
      const error = asObject(obj?.error);
      const result = asObject(obj?.result);
      const rpcErr = rpcErrorMessage(raw);
      if (rpcErr) {
        if (error?.code === -32603 && obj?.id !== expectedId) return;
        fail(rpcErr);
        return;
      }
      if (obj?.id !== expectedId || !result) return;
      if (expectedId === 1) {
        sendSessionNew();
        return;
      }
      if (expectedId === 2) {
        const models = normalizeModels(result.models, defaultModelOption, result.configOptions);
        finish(resolve, models);
        if (!child.killed) child.kill("SIGTERM");
      }
    });
    child.stdout.on("data", (chunk) => parser.feed(chunk));
    child.stdout.on("close", () => parser.flush());
    child.stdin.on("error", (err) => fail(`stdin error: ${err.message}`));
    child.stderr.on("data", (chunk) => {
      stderrBuf = `${stderrBuf}${chunk}`.slice(-16e3);
    });
    child.on("error", (err) => fail(`spawn failed: ${err.message}`));
    child.on("close", (code, signal) => {
      parser.flush();
      if (!settled) {
        const errTail = stderrBuf.trim();
        const suffix = errTail ? ` stderr=${errTail}` : "";
        fail(`ACP model detection exited code=${code} signal=${signal ?? "none"}${suffix}`);
      }
    });
    if (effectiveTimeoutMs > 0) {
      timer = setTimeout(() => {
        fail(`ACP model detection timed out after ${effectiveTimeoutMs}ms`);
      }, effectiveTimeoutMs);
    }
    writeRpc(1, "initialize", {
      protocolVersion: ACP_PROTOCOL_VERSION,
      clientCapabilities: { terminal: false },
      clientInfo: { name: clientName, version: clientVersion }
    });
  });
}

// runtime-bridge/catalog.json
var catalog_default = [
  {
    id: "aider",
    name: "Aider",
    bins: [
      "aider"
    ]
  },
  {
    id: "amp",
    name: "Amp",
    bins: [
      "amp"
    ]
  },
  {
    id: "amr",
    name: "AMR",
    bins: [
      "vela"
    ]
  },
  {
    id: "antigravity",
    name: "Antigravity",
    bins: [
      "agy"
    ]
  },
  {
    id: "atomcode",
    name: "AtomCode CLI",
    bins: [
      "atomcode"
    ]
  },
  {
    id: "byok-opencode",
    name: "BYOK OpenCode",
    bins: [
      "opencode-cli",
      "opencode"
    ]
  },
  {
    id: "claude",
    name: "Claude Code",
    bins: [
      "claude",
      "openclaude"
    ]
  },
  {
    id: "codebuddy",
    name: "Codebuddy Code",
    bins: [
      "codebuddy",
      "cbc"
    ]
  },
  {
    id: "codex",
    name: "Codex CLI",
    bins: [
      "codex"
    ]
  },
  {
    id: "copilot",
    name: "GitHub Copilot CLI",
    bins: [
      "copilot"
    ]
  },
  {
    id: "cursor-agent",
    name: "Cursor Agent",
    bins: [
      "cursor-agent"
    ]
  },
  {
    id: "deepseek-harness",
    name: "DeepSeek Harness",
    bins: [
      "dsh"
    ]
  },
  {
    id: "deepseek",
    name: "DeepSeek TUI",
    bins: [
      "deepseek",
      "codewhale"
    ]
  },
  {
    id: "devin",
    name: "Devin for Terminal",
    bins: [
      "devin"
    ]
  },
  {
    id: "grok-build",
    name: "Grok Build",
    bins: [
      "grok"
    ]
  },
  {
    id: "hermes",
    name: "Hermes",
    bins: [
      "hermes"
    ]
  },
  {
    id: "kilo",
    name: "Kilo",
    bins: [
      "kilo"
    ]
  },
  {
    id: "kimi",
    name: "Kimi CLI",
    bins: [
      "kimi"
    ]
  },
  {
    id: "kiro",
    name: "Kiro CLI",
    bins: [
      "kiro-cli"
    ]
  },
  {
    id: "mimo",
    name: "MiMo Code",
    bins: [
      "mimo"
    ]
  },
  {
    id: "opencode",
    name: "OpenCode",
    bins: [
      "opencode-cli",
      "opencode"
    ]
  },
  {
    id: "pi",
    name: "Pi",
    bins: [
      "pi"
    ]
  },
  {
    id: "qoder",
    name: "Qoder CLI",
    bins: [
      "qodercli"
    ]
  },
  {
    id: "qwen",
    name: "Qwen Code",
    bins: [
      "qwen"
    ]
  },
  {
    id: "reasonix",
    name: "DeepSeek Reasonix",
    bins: [
      "reasonix",
      "dsnix"
    ]
  },
  {
    id: "trae-cli",
    name: "Trae CLI",
    bins: [
      "traecli"
    ]
  },
  {
    id: "vibe",
    name: "Mistral Vibe CLI",
    bins: [
      "vibe-acp"
    ]
  }
];

// third_party/open-design/runtime-models/models.ts
var DEFAULT_MODEL_OPTION = {
  id: "default",
  label: "Default (CLI config)"
};
function sanitizeCustomModel(id) {
  if (typeof id !== "string") return null;
  const trimmed = id.trim();
  if (trimmed.length === 0 || trimmed.length > 200) return null;
  if (!/^[A-Za-z0-9][A-Za-z0-9._/:@-]*$/.test(trimmed)) return null;
  return trimmed;
}

// third_party/open-design/runtime-models/mmd-routes.ts
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
var DEFAULT_MMD_MODEL_ROUTES_FILE = join(".config", "mms", "model-routes.json");
var MMD_MODEL_ROUTES_FILE_ENV = "MMD_MODEL_ROUTES_FILE";
function stringEnv(env2, key) {
  const value = env2[key];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}
function resolveHome(env2) {
  return stringEnv(env2, "HOME") ?? homedir() ?? null;
}
function expandRoutesFileOverride(raw, env2) {
  if (raw === "~") return resolveHome(env2);
  if (raw.startsWith("~/") || raw.startsWith("~\\")) {
    const home = resolveHome(env2);
    return home ? join(home, raw.slice(2)) : null;
  }
  return raw;
}
function resolveMmdRoutesFile(env2) {
  const override = stringEnv(env2, MMD_MODEL_ROUTES_FILE_ENV);
  if (override) return expandRoutesFileOverride(override, env2);
  const home = resolveHome(env2);
  if (!home) return null;
  return join(home, DEFAULT_MMD_MODEL_ROUTES_FILE);
}
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function parseMmdRouteModelIds(raw) {
  if (!isRecord(raw) || !isRecord(raw.routes)) return [];
  const seen = /* @__PURE__ */ new Set();
  const ids = [];
  for (const rawId of Object.keys(raw.routes)) {
    const id = sanitizeCustomModel(rawId);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids;
}
function resolveMmdRouteLaunchEnv(raw, modelId) {
  const id = sanitizeCustomModel(modelId);
  if (!id || !isRecord(raw) || !isRecord(raw.routes)) return null;
  const route = raw.routes[id];
  if (!isRecord(route) || !isRecord(route.primary)) return null;
  const baseUrl = typeof route.primary.anthropic_base_url === "string" ? route.primary.anthropic_base_url.trim() : "";
  if (!baseUrl) return null;
  const apiKey = typeof route.primary.api_key === "string" ? route.primary.api_key.trim() : "";
  return {
    ANTHROPIC_BASE_URL: baseUrl,
    ...apiKey ? { ANTHROPIC_AUTH_TOKEN: apiKey } : {}
  };
}
function addModel(out, seen, option) {
  const id = sanitizeCustomModel(option.id);
  if (!id || seen.has(id)) return;
  seen.add(id);
  const label = typeof option.label === "string" && option.label.trim().length > 0 ? option.label : id;
  out.push({ id, label });
}
function mergeMmdRouteModels(routeIds, fallbackModels) {
  const out = [];
  const seen = /* @__PURE__ */ new Set();
  addModel(out, seen, DEFAULT_MODEL_OPTION);
  for (const routeId of routeIds) {
    addModel(out, seen, { id: routeId, label: routeId });
  }
  for (const model of fallbackModels) {
    addModel(out, seen, model);
  }
  return out;
}
async function loadMmdRouteModels(env2, fallbackModels) {
  const routesFile = resolveMmdRoutesFile(env2);
  if (!routesFile) return null;
  let text;
  try {
    text = await readFile(routesFile, "utf8");
  } catch {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  const routeIds = parseMmdRouteModelIds(parsed);
  if (routeIds.length === 0) return null;
  return mergeMmdRouteModels(routeIds, fallbackModels);
}
async function loadMmdRouteLaunchEnv(env2, modelId) {
  const routesFile = resolveMmdRoutesFile(env2);
  if (!routesFile) return null;
  let text;
  try {
    text = await readFile(routesFile, "utf8");
  } catch {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  return resolveMmdRouteLaunchEnv(parsed, modelId);
}

// third_party/open-design/runtime-models/codex-models.ts
function parseCodexStringList(raw) {
  if (!Array.isArray(raw)) return void 0;
  const values = raw.map((value) => typeof value === "string" ? value.trim() : "").filter(Boolean);
  return values.length > 0 ? values : void 0;
}
function parseCodexServiceTiers(raw) {
  if (!Array.isArray(raw)) return void 0;
  const out = [];
  const seen = /* @__PURE__ */ new Set();
  for (const tier of raw) {
    if (!tier || typeof tier !== "object") continue;
    const entry = tier;
    const id = typeof entry.id === "string" ? entry.id.trim() : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const label = typeof entry.name === "string" && entry.name.trim() ? entry.name.trim() : typeof entry.label === "string" && entry.label.trim() ? entry.label.trim() : id;
    out.push({ id, label });
  }
  return out.length > 0 ? out : void 0;
}
var CODEX_SPEED_TIER_SERVICE_TIER_OPTIONS = {
  fast: { id: "priority", label: "Fast" }
};
function parseCodexServiceTiersFromSpeedTiers(speedTiers) {
  if (!speedTiers) return void 0;
  const out = [];
  const seen = /* @__PURE__ */ new Set();
  for (const raw of speedTiers) {
    const option = CODEX_SPEED_TIER_SERVICE_TIER_OPTIONS[raw.toLowerCase()];
    if (!option || seen.has(option.id)) continue;
    seen.add(option.id);
    out.push({ ...option });
  }
  return out.length > 0 ? out : void 0;
}
function parseCodexDebugModels(stdout) {
  let parsed;
  try {
    parsed = JSON.parse(String(stdout || ""));
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const models = Array.isArray(parsed) ? parsed : parsed.models;
  if (!Array.isArray(models)) return null;
  const out = [DEFAULT_MODEL_OPTION];
  const seen = /* @__PURE__ */ new Set([DEFAULT_MODEL_OPTION.id]);
  for (const raw of models) {
    if (!raw || typeof raw !== "object") continue;
    const entry = raw;
    if (entry.visibility === "hidden") continue;
    const id = typeof entry.slug === "string" ? entry.slug.trim() : typeof entry.id === "string" ? entry.id.trim() : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const label = typeof entry.display_name === "string" && entry.display_name.trim() ? entry.display_name.trim() : typeof entry.name === "string" && entry.name.trim() ? entry.name.trim() : id;
    const model = { id, label };
    const additionalSpeedTiers = parseCodexStringList(
      entry.additional_speed_tiers
    );
    if (additionalSpeedTiers) model.additionalSpeedTiers = additionalSpeedTiers;
    const serviceTierOptions = parseCodexServiceTiers(entry.service_tiers) ?? parseCodexServiceTiersFromSpeedTiers(additionalSpeedTiers);
    if (serviceTierOptions) model.serviceTierOptions = serviceTierOptions;
    out.push(model);
  }
  return out.length > 1 ? out : null;
}

// third_party/open-design/runtime-models/opencode-models.ts
var OPENCODE_VARIANT_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/u;
var OPENCODE_MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._/:@-]*$/u;
function reasoningOptions(ids) {
  return [
    { id: "default", label: "Default" },
    ...ids.map((id) => ({ id, label: id }))
  ];
}
function parseVerboseModelMetadata(lines, start) {
  let buffer = "";
  for (let index = start; index < lines.length; index += 1) {
    buffer += `${lines[index]}
`;
    if (!lines[index].trimEnd().endsWith("}")) continue;
    try {
      const parsed = JSON.parse(buffer);
      return {
        value: parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null,
        end: index
      };
    } catch {
    }
  }
  return { value: null, end: start - 1 };
}
function parseOpenCodeModels(stdout) {
  const lines = String(stdout || "").split("\n");
  const models = [DEFAULT_MODEL_OPTION];
  const seen = /* @__PURE__ */ new Set();
  for (let index = 0; index < lines.length; index += 1) {
    const id = lines[index].trim();
    if (!OPENCODE_MODEL_ID.test(id) || seen.has(id)) continue;
    seen.add(id);
    const next = lines[index + 1]?.trimStart();
    const metadata = next?.startsWith("{") ? parseVerboseModelMetadata(lines, index + 1) : { value: null, end: index };
    const variants = metadata.value?.["variants"];
    const variantIds = variants && typeof variants === "object" && !Array.isArray(variants) ? Object.keys(variants).filter((variant) => OPENCODE_VARIANT_ID.test(variant)) : [];
    models.push({
      id,
      label: id,
      ...variantIds.length > 0 ? { reasoningOptions: reasoningOptions(variantIds) } : {}
    });
    index = Math.max(index, metadata.end);
  }
  return models.length > 1 ? models : null;
}

// third_party/open-design/runtime-models/fallbacks.json
var fallbacks_default = {
  claude: [
    {
      id: "sonnet",
      label: "Sonnet (alias)"
    },
    {
      id: "opus",
      label: "Opus (alias)"
    },
    {
      id: "haiku",
      label: "Haiku (alias)"
    },
    {
      id: "fable",
      label: "Fable (alias)"
    },
    {
      id: "claude-opus-5",
      label: "claude-opus-5"
    },
    {
      id: "claude-sonnet-5",
      label: "claude-sonnet-5"
    },
    {
      id: "claude-fable-5",
      label: "claude-fable-5"
    },
    {
      id: "claude-opus-4-5",
      label: "claude-opus-4-5"
    },
    {
      id: "claude-sonnet-4-5",
      label: "claude-sonnet-4-5"
    },
    {
      id: "claude-haiku-4-5",
      label: "claude-haiku-4-5"
    }
  ],
  kimi: [
    {
      id: "kimi-k2-turbo-preview",
      label: "kimi-k2-turbo-preview"
    },
    {
      id: "moonshot-v1-8k",
      label: "moonshot-v1-8k"
    },
    {
      id: "moonshot-v1-32k",
      label: "moonshot-v1-32k"
    }
  ],
  hermes: [
    {
      id: "grok-4.3",
      label: "grok-4.3 (xAI \xB7 default)"
    },
    {
      id: "grok-4.20-reasoning",
      label: "grok-4.20-reasoning (xAI \xB7 deep)"
    },
    {
      id: "grok-4.20-0309-non-reasoning",
      label: "grok-4.20-non-reasoning (xAI \xB7 fast)"
    },
    {
      id: "grok-4.20-multi-agent-0309",
      label: "grok-4.20-multi-agent (xAI \xB7 orchestration)"
    },
    {
      id: "openai-codex:gpt-5.5",
      label: "gpt-5.5 (openai-codex:gpt-5.5)"
    },
    {
      id: "openai-codex:gpt-5.4",
      label: "gpt-5.4 (openai-codex:gpt-5.4)"
    },
    {
      id: "openai-codex:gpt-5.4-mini",
      label: "gpt-5.4-mini (openai-codex:gpt-5.4-mini)"
    }
  ],
  codex: [
    {
      id: "gpt-5.5",
      label: "gpt-5.5"
    },
    {
      id: "gpt-5.4",
      label: "gpt-5.4"
    },
    {
      id: "gpt-5.4-mini",
      label: "gpt-5.4-mini"
    },
    {
      id: "gpt-5.3-codex",
      label: "gpt-5.3-codex"
    },
    {
      id: "gpt-5.1",
      label: "gpt-5.1"
    },
    {
      id: "gpt-5.1-codex-mini",
      label: "gpt-5.1-codex-mini"
    },
    {
      id: "gpt-5-codex",
      label: "gpt-5-codex"
    },
    {
      id: "gpt-5",
      label: "gpt-5"
    },
    {
      id: "o3",
      label: "o3"
    },
    {
      id: "o4-mini",
      label: "o4-mini"
    }
  ],
  opencode: [
    {
      id: "anthropic/claude-sonnet-4-5",
      label: "anthropic/claude-sonnet-4-5"
    },
    {
      id: "openai/gpt-5.6-sol",
      label: "openai/gpt-5.6-sol"
    },
    {
      id: "openai/gpt-5.6-terra",
      label: "openai/gpt-5.6-terra"
    },
    {
      id: "openai/gpt-5.6-luna",
      label: "openai/gpt-5.6-luna"
    },
    {
      id: "openai/gpt-5",
      label: "openai/gpt-5"
    },
    {
      id: "google/gemini-2.5-pro",
      label: "google/gemini-2.5-pro"
    }
  ]
};

// runtime-bridge/main.ts
var exec = promisify(execFile);
var acpArgs = { kimi: ["acp"], hermes: ["acp"], reasonix: ["acp"], kilo: ["acp"], kiro: ["acp"], vibe: [] };
var active = /* @__PURE__ */ new Map();
var defaults = [{ id: "default", label: "\u5BBF\u4E3B\u9ED8\u8BA4\u6A21\u578B" }];
function claudeConfiguredModel() {
  try {
    const file = JSON.parse(readFileSync(path2.join(homedir2(), ".claude", "settings.json"), "utf8"));
    const alias = typeof file?.model === "string" ? file.model.trim() : "";
    const configured = file?.env && typeof file.env === "object" ? file.env : {};
    const merged = { ...configured, ...env };
    const concrete = (alias ? merged["ANTHROPIC_DEFAULT_" + alias.toUpperCase() + "_MODEL"] : "") || merged.ANTHROPIC_MODEL || "";
    return { alias, concrete: typeof concrete === "string" ? concrete.trim() : "" };
  } catch {
    return { alias: "", concrete: "" };
  }
}
function hostDefaultLabel(id) {
  if (id !== "claude") return defaults[0].label;
  const { alias, concrete } = claudeConfiguredModel();
  if (concrete && alias) return `\u9ED8\u8BA4\uFF1A${concrete}\uFF08\u522B\u540D ${alias}\uFF09`;
  if (concrete) return `\u9ED8\u8BA4\uFF1A${concrete}`;
  if (alias) return `\u9ED8\u8BA4\uFF1A${alias}`;
  return defaults[0].label;
}
function hostDefaults(id) {
  return [{ id: "default", label: hostDefaultLabel(id) }];
}
var env = { ...process.env };
delete env.CLAUDECODE;
var dirs = [...(env.PATH || "").split(path2.delimiter), path2.join(homedir2(), ".local/bin"), path2.join(homedir2(), ".kimi-code/bin"), path2.join(homedir2(), ".opencode/bin"), path2.join(homedir2(), ".npm-global/bin"), path2.join(homedir2(), ".bun/bin"), path2.join(homedir2(), ".cargo/bin"), path2.join(homedir2(), ".dsh/bin"), "/opt/homebrew/bin", "/usr/local/bin"];
env.PATH = [...new Set(dirs)].join(path2.delimiter);
function findBin(def, custom) {
  for (const f of custom ? [path2.resolve(custom)] : def.bins.flatMap((b) => dirs.map((d) => path2.join(d, b)))) {
    try {
      accessSync(f, constants.X_OK);
      return f;
    } catch {
    }
  }
  return null;
}
function defFor(id) {
  const d = catalog_default.find((d2) => d2.id === id);
  if (!d) throw Error("Unknown runtime: " + id);
  return d;
}
function wire(value) {
  process.stdout.write(JSON.stringify(value) + "\n");
}
function emit(id, kind, data = {}) {
  const state = active.get(id);
  if (state && (kind === "text" && data.text?.trim() || kind === "tool")) state.publicActivity = true;
  wire({ method: "event", params: { execution_id: id, kind, ...data } });
}
function protocol(id) {
  return id in acpArgs ? "acp" : id === "claude" ? "claude-stream-json" : id === "mimo" ? "opencode-json" : id === "codex" || id === "opencode" ? "native-manager" : null;
}
function capabilities(id) {
  const p = protocol(id);
  return { chat: !!p, cancel: !!p, resume: p === "acp" ? "negotiated" : p === "claude-stream-json" || p === "opencode-json", images: p === "acp" ? "negotiated" : p === "claude-stream-json", questions: p === "acp", steer: false, read_only: false, network_control: false, permission_modes: ["runtime-native"] };
}
function terminate(child) {
  if (!child?.pid) return;
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    try {
      child.kill("SIGTERM");
    } catch {
    }
  }
  setTimeout(() => {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
    }
  }, 1200).unref();
}
function launch(bin, args, cwd, childEnv = env) {
  return spawn2(bin, args, { cwd, env: childEnv, stdio: ["pipe", "pipe", "pipe"], detached: process.platform !== "win32" });
}
function connect(bin, args, cwd, onUpdate, onRequest) {
  const child = launch(bin, args, cwd);
  let seq = 0;
  const pending = /* @__PURE__ */ new Map();
  const send = (v) => child.stdin.write(JSON.stringify(v) + "\n");
  const parser = createJsonLineStream((m) => {
    if (m.method) {
      if (m.id !== void 0) onRequest(m, (result) => send({ jsonrpc: "2.0", id: m.id, result }));
      else onUpdate(m);
    } else if (pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      clearTimeout(p.timer);
      m.error ? p.reject(Error(m.error.message || "ACP error")) : p.resolve(m.result);
    }
  });
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (c) => parser.feed(c));
  child.stderr.resume();
  const fail = (e) => {
    for (const p of pending.values()) {
      clearTimeout(p.timer);
      p.reject(e);
    }
    pending.clear();
  };
  child.on("error", fail);
  child.stdin.on("error", fail);
  child.on("close", (code) => {
    parser.flush();
    fail(Error("ACP exited before response: " + code));
  });
  return { child, notify: (method, params) => send({ jsonrpc: "2.0", method, params }), call: (method, params, timeout = 2e4) => new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = timeout > 0 ? setTimeout(() => {
      pending.delete(id);
      reject(Error(method + " timed out"));
    }, timeout) : null;
    pending.set(id, { resolve, reject, timer });
    send({ jsonrpc: "2.0", id, method, params });
  }) };
}
async function handshake(conn, p) {
  const init = await conn.call("initialize", { protocolVersion: 1, clientCapabilities: { fs: { readTextFile: false, writeTextFile: false }, terminal: false }, clientInfo: { name: "briefloop", version: "1" } });
  if (p.session_id && !init.agentCapabilities?.loadSession) throw Error("Host does not advertise session/load");
  const session = await conn.call(p.session_id ? "session/load" : "session/new", { ...buildAcpSessionNewParams(p.cwd), ...p.session_id ? { sessionId: p.session_id } : {} });
  return { init, session };
}
async function discover(p) {
  return await Promise.all(catalog_default.filter((d) => d.id !== "byok-opencode").map(async (d) => {
    const bin = findBin(d, p.paths?.[d.id]);
    if (!bin) return { ...d, path: null, installed: false, status: "not_installed", capabilities: capabilities(d.id) };
    let version = null, error = null;
    try {
      const r = await exec(bin, ["--version"], { env, timeout: 5e3, maxBuffer: 16384 });
      version = r.stdout.trim().split("\n")[0].slice(0, 160);
    } catch {
      error = "Version probe failed";
    }
    const impl = protocol(d.id);
    return { ...d, path: bin, installed: true, version, status: impl ? "detected" : "not_integrated", protocol: impl, implemented: !!impl, error, capabilities: capabilities(d.id) };
  }));
}
async function listModels(p) {
  const d = defFor(p.runtime_id), bin = findBin(d, p.path);
  if (!bin) throw Error("Runtime not installed");
  if (p.runtime_id === "reasonix") {
    const r = await exec(bin, ["doctor", "--json"], { env, cwd: p.cwd || process.cwd(), timeout: 1e4, maxBuffer: 1024 * 1024 });
    const d2 = JSON.parse(r.stdout);
    return { models: [...defaults, ...(d2.providers || []).filter((x) => typeof x.name === "string").map((x) => ({ id: x.name, label: x.name + (x.model ? " \xB7 " + x.model : ""), provider: x.kind || "configured", model_id: x.model }))], source: "native_config", note: "Models declared by the host; account availability is checked by a model call." };
  }
  const fallback = [...hostDefaults(p.runtime_id), ...fallbacks_default[p.runtime_id] || []];
  if (p.runtime_id === "claude") {
    const routed = await loadMmdRouteModels(env, fallback);
    return { models: routed || fallback, source: routed ? "local_routes" : "builtin_hints", note: "\u5185\u7F6E\u9009\u9879\u4E0E\u5DF2\u914D\u7F6E\u8DEF\u7531\uFF1B\u53EF\u624B\u52A8\u8F93\u5165\u5176\u4ED6\u6A21\u578B ID\u3002" };
  }
  try {
    if (p.runtime_id === "codex") {
      const r = await exec(bin, ["debug", "models"], { env, timeout: 5e3, maxBuffer: 4 * 1024 * 1024 });
      const models = parseCodexDebugModels(r.stdout);
      return { models: models || fallback, source: models ? "host" : "builtin_hints" };
    }
    if (["mimo", "opencode"].includes(p.runtime_id)) {
      const r = await exec(bin, ["models", "--verbose"], { env, timeout: 2e4, maxBuffer: 8 * 1024 * 1024 });
      const models = parseOpenCodeModels(r.stdout);
      return { models: models || fallback, source: models ? "host" : "builtin_hints" };
    }
    if (p.runtime_id in acpArgs) {
      const models = await detectAcpModels({ bin, args: acpArgs[p.runtime_id], cwd: p.cwd || process.cwd(), env, timeoutMs: 15e3, defaultModelOption: defaults[0], clientName: "briefloop-models" });
      const live = models.some((m) => m.id !== "default");
      return { models: live ? models : fallback, source: live ? "host" : "builtin_hints" };
    }
  } catch {
    return { models: fallback, source: "builtin_hints", diagnostic: "\u5BBF\u4E3B\u76EE\u5F55\u8BFB\u53D6\u5931\u8D25\uFF0C\u5DF2\u663E\u793A\u5185\u7F6E\u5EFA\u8BAE\uFF1B\u4E5F\u53EF\u76F4\u63A5\u8F93\u5165\u6A21\u578B ID\u3002" };
  }
  return { models: fallback, source: "builtin_hints" };
}
function validate(p) {
  if (p.model && !sanitizeCustomModel(p.model)) throw Error("Invalid model ID");
  if (!p.execution_id || !p.cwd || typeof p.prompt !== "string") throw Error("execution_id, cwd and prompt required");
  if (active.has(p.execution_id)) throw Error("Execution already active");
  if ((p.permission || "runtime-native") !== "runtime-native") throw Error("This runtime cannot enforce " + p.permission + "; use runtime-native or a restricted native manager");
  if (p.allow_web === false) throw Error("This runtime cannot enforce network disabled; enable host-native network access or choose a native manager");
  const d = defFor(p.runtime_id), bin = findBin(d, p.path);
  if (!bin) throw Error("Runtime not installed");
  if (!protocol(d.id) || protocol(d.id) === "native-manager") throw Error("Runtime execution belongs to native manager or is not integrated");
  return bin;
}
async function runAcp(p, state) {
  let sessionId;
  const args = [...acpArgs[p.runtime_id]];
  if (p.runtime_id === "reasonix" && p.model && p.model !== "default") args.push("-model", p.model);
  const conn = connect(state.bin, args, p.cwd, (m) => {
    if (m.method !== "session/update" || !state.promptStarted) return;
    const u = m.params?.update || {};
    if (u.sessionUpdate === "agent_message_chunk" && u.content?.type === "text") emit(p.execution_id, "text", { text: u.content.text, delta: true });
    else if (["tool_call", "tool_call_update"].includes(u.sessionUpdate) && !["think", "thinking", "reasoning"].includes(u.kind)) emit(p.execution_id, "tool", { id: u.toolCallId, name: u.title || u.kind || "Tool", status: u.status, input: u.rawInput, output: u.rawOutput });
    else if (u.sessionUpdate === "usage_update") emit(p.execution_id, "usage", { usage: u.usage || u });
  }, (m, reply) => {
    if (m.method === "session/request_permission") {
      const id = String(m.id);
      state.questions.set(id, { reply, options: m.params?.options || [] });
      emit(p.execution_id, "question", { request_id: id, type: "permission", title: m.params?.toolCall?.title || "Runtime permission", options: m.params?.options || [] });
    } else {
      reply({ error: "Client method unsupported" });
    }
  });
  state.child = conn.child;
  state.cancel = () => {
    if (sessionId) conn.notify("session/cancel", { sessionId });
    terminate(conn.child);
  };
  try {
    const { init, session } = await handshake(conn, p);
    sessionId = p.session_id || session.sessionId;
    if (!sessionId) throw Error("No session ID returned");
    emit(p.execution_id, "session", { session_id: sessionId, capabilities: init.agentCapabilities || {} });
    if (p.model && p.model !== "default" && p.runtime_id !== "reasonix") {
      const cfg = findModelConfigOption(session.configOptions);
      await conn.call(cfg ? "session/set_config_option" : "session/set_model", cfg ? { sessionId, configId: cfg.configId, value: p.model } : { sessionId, modelId: p.model });
    }
    const blocks = buildPromptBlocks(p.prompt, []);
    if (p.images?.length && !init.agentCapabilities?.promptCapabilities?.image) throw Error("Host does not advertise image input");
    for (const image of p.images || []) {
      const f = typeof image === "string" ? image : image.path;
      const mime = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp" }[path2.extname(f).toLowerCase()];
      if (!mime) throw Error("Unsupported image format");
      const data = readFileSync(f);
      if (data.length > 20 * 1024 * 1024) throw Error("Image exceeds 20 MiB");
      blocks.push({ type: "image", mimeType: mime, data: data.toString("base64") });
    }
    state.promptStarted = true;
    const result = await conn.call("session/prompt", { sessionId, prompt: blocks }, p.timeout_ms || 0);
    if (result?.usage) emit(p.execution_id, "usage", { usage: result.usage });
    if (result?.stopReason === "cancelled") state.cancelled = true;
  } finally {
    terminate(conn.child);
  }
}
async function runStream(p, state) {
  const claude = p.runtime_id === "claude";
  let args = claude ? ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"] : ["run", "--format", "json"];
  if (p.model && p.model !== "default") args.push("--model", p.model);
  if (p.session_id) args.push(claude ? "--resume" : "--session", p.session_id);
  if (claude && p.web_tools === true) args.push("--allowedTools", "WebSearch", "WebFetch");
  if (!claude && p.images?.length) throw Error("MiMo direct image transport not verified");
  const route = claude ? await loadMmdRouteLaunchEnv(env, p.model) : null;
  const child = launch(state.bin, args, p.cwd, route ? { ...env, ...route } : env);
  state.child = child;
  state.cancel = () => terminate(child);
  let resultSeen = false, lastSession = null;
  await new Promise((resolve, reject) => {
    const timer = p.timeout_ms ? setTimeout(() => {
      terminate(child);
      reject(Error("Runtime turn timed out"));
    }, p.timeout_ms) : null;
    const parser = createJsonLineStream((m) => {
      const sid = m.session_id || m.sessionID;
      if (sid && sid !== lastSession) {
        lastSession = sid;
        emit(p.execution_id, "session", { session_id: sid });
      }
      if (claude) {
        if (m.type === "assistant") {
          for (const b of m.message?.content || []) {
            if (b.type === "text") emit(p.execution_id, "text", { text: b.text, delta: true });
            if (b.type === "tool_use" && !/^(think|thinking|reasoning)$/i.test(b.name)) emit(p.execution_id, "tool", { id: b.id, name: b.name, status: "running", input: b.input });
          }
        }
        if (m.type === "user") {
          for (const b of m.message?.content || []) if (b.type === "tool_result") emit(p.execution_id, "tool", { id: b.tool_use_id, status: b.is_error ? "failed" : "completed", output: b.content });
        }
        if (m.type === "result") {
          resultSeen = true;
          if (m.usage) emit(p.execution_id, "usage", { usage: m.usage });
          if (m.is_error) {
            reject(Error("Host reported unsuccessful result"));
            terminate(child);
          }
        }
      } else {
        const part = m.part || {};
        if (m.type === "text") emit(p.execution_id, "text", { text: part.text || m.text || "", delta: true });
        if (m.type === "tool_use" && !/^(think|thinking|reasoning)$/i.test(part.tool)) emit(p.execution_id, "tool", { id: part.callID, name: part.tool, status: part.state?.status, input: part.state?.input, output: part.state?.output });
        if (m.type === "step_finish") {
          resultSeen = true;
          emit(p.execution_id, "usage", { usage: part.tokens || {}, cost: part.cost });
        }
        if (m.type === "error") {
          reject(Error([m.error?.name || "Host error", m.error?.data?.statusCode ? "HTTP " + m.error.data.statusCode : ""].filter(Boolean).join(" \xB7 ")));
          terminate(child);
        }
      }
    });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (c) => parser.feed(c));
    child.stderr.resume();
    child.on("error", (e) => {
      clearTimeout(timer);
      reject(e);
    });
    child.stdin.on("error", () => {
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      parser.flush();
      if (state.cancelled) resolve();
      else if (code === 0 && resultSeen) resolve();
      else reject(Error("Runtime exited without successful result (code " + code + ")"));
    });
    if (claude) {
      const content = [{ type: "text", text: p.prompt }];
      for (const img of p.images || []) {
        const f = typeof img === "string" ? img : img.path;
        const mime = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp" }[path2.extname(f).toLowerCase()];
        if (!mime) {
          terminate(child);
          reject(Error("Unsupported image"));
          return;
        }
        const data = readFileSync(f);
        if (data.length > 20 * 1024 * 1024) {
          terminate(child);
          reject(Error("Image exceeds 20 MiB"));
          return;
        }
        content.push({ type: "image", source: { type: "base64", media_type: mime, data: data.toString("base64") } });
      }
      child.stdin.end(JSON.stringify({ type: "user", message: { role: "user", content } }) + "\n");
    } else child.stdin.end(p.prompt);
  });
}
async function execute(p, state) {
  try {
    if (state.cancelled) return;
    if (p.runtime_id in acpArgs) await runAcp(p, state);
    else await runStream(p, state);
    if (!state.cancelled && !state.publicActivity) throw Error("Host ended without visible output or tool activity; verify host configuration");
    emit(p.execution_id, "end", { status: state.cancelled ? "cancelled" : "completed" });
  } catch (e) {
    if (!state.cancelled) emit(p.execution_id, "error", { message: String(e.message || e) });
    emit(p.execution_id, "end", { status: state.cancelled ? "cancelled" : "failed", error: state.cancelled ? void 0 : String(e.message || e) });
  } finally {
    state.questions.clear();
    active.delete(p.execution_id);
  }
}
async function handle(method, p) {
  if (method === "discover") return discover(p);
  if (method === "list_models") return listModels(p);
  if (method === "start") {
    const bin = validate(p);
    const state = { bin, cancelled: false, questions: /* @__PURE__ */ new Map() };
    active.set(p.execution_id, state);
    setImmediate(() => execute(p, state));
    return { execution_id: p.execution_id };
  }
  if (method === "cancel") {
    const s = active.get(p.execution_id);
    if (!s) return { cancelled: false };
    s.cancelled = true;
    s.cancel?.();
    return { cancelled: true };
  }
  if (method === "answer") {
    const s = active.get(p.execution_id), q = s?.questions.get(String(p.request_id));
    if (!q) throw Error("Request no longer pending");
    if (p.option_id && !q.options.some((o) => o.optionId === p.option_id)) throw Error("Unknown permission option");
    q.reply({ outcome: p.option_id ? { outcome: "selected", optionId: p.option_id } : { outcome: "cancelled" } });
    s.questions.delete(String(p.request_id));
    return { accepted: true };
  }
  throw Error("Unknown bridge method");
}
var input = createInterface({ input: process.stdin });
input.on("line", async (line) => {
  let m;
  try {
    m = JSON.parse(line);
    wire({ id: m.id, result: await handle(m.method, m.params || {}) });
  } catch (e) {
    wire({ id: m?.id ?? null, error: { message: String(e.message || e) } });
  }
});
function shutdown() {
  for (const s of active.values()) {
    s.cancelled = true;
    s.cancel?.();
  }
  setTimeout(() => process.exit(0), 1500).unref();
}
input.on("close", shutdown);
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
