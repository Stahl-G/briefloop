// Model-visible tool discovery over a closed, runner-declared registry. Loading
// never introduces executable code, a provider, or a filesystem capability.
import { createHash } from "node:crypto";
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";

export const LOAD_TOOLS = "load_tools";
export const TOOL_LOADING_ENTRY = "briefloop.tool-loading.v1";
export const LOAD_TOOLS_GUIDE = "按能力组启用工具。必须单独调用 load_tools({groups:[组名]})，等待成功回执，在下一轮使用新工具；不得与任何其他工具并列调用。重复加载同组不改变状态。";

interface Group { name: string; description: string; tools: string[]; guide?: string; }
interface LoadingConfig { initial: string[]; groups: Group[]; }
interface StoredState { fingerprint: string; groups: string[]; }
type StoredEntry = { type: string; customType?: string; data?: unknown };
const GROUP_NAME = /^[a-z][a-z0-9_-]{0,63}$/;

function names(value: unknown, field: string, allowed: Set<string>): string[] {
  if (!Array.isArray(value) || value.some(name => typeof name !== "string" || !allowed.has(name))) {
    throw new Error(`${field} must contain only registered tool names`);
  }
  return [...new Set(value as string[])];
}

export class ToolLoading {
  readonly config: LoadingConfig;
  readonly fingerprint: string;
  private loaded = new Set<string>();
  readonly restoreStatus: "initial" | "restored" | "configuration_changed" | "invalid_state";

  constructor(value: unknown, readonly allowed: string[], entries: StoredEntry[], identity: unknown) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("tool_loading must be an object");
    const raw = value as Record<string, unknown>, allow = new Set(allowed);
    if (allow.has(LOAD_TOOLS)) throw new Error("load_tools is reserved for the engine");
    const initial = names(raw.initial, "tool_loading.initial", allow);
    if (!Array.isArray(raw.groups)) throw new Error("tool_loading.groups must be an array");
    const used = new Set<string>();
    const groups = raw.groups.map((item): Group => {
      if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("invalid tool group");
      const group = item as Record<string, unknown>;
      if (typeof group.name !== "string" || !GROUP_NAME.test(group.name) || used.has(group.name)) throw new Error("tool group names must be unique identifiers");
      if (typeof group.description !== "string" || !group.description.trim()) throw new Error("tool group description required");
      if (group.guide !== undefined && typeof group.guide !== "string") throw new Error("tool group guide must be text");
      used.add(group.name);
      const tools = names(group.tools, `tool_loading.groups.${group.name}.tools`, allow);
      if (!tools.length) throw new Error("tool group must contain at least one registered tool");
      return { name: group.name, description: group.description, tools, ...(group.guide !== undefined ? { guide: group.guide as string } : {}) };
    });
    this.config = { initial, groups };
    this.fingerprint = createHash("sha256").update(JSON.stringify({ config: this.config, allowed, identity })).digest("hex");
    const previous = [...entries].reverse().find(entry => entry.type === "custom" && entry.customType === TOOL_LOADING_ENTRY);
    this.restoreStatus = "initial";
    if (previous) {
      const state = previous.data as Partial<StoredState> | undefined;
      if (!state || state.fingerprint !== this.fingerprint) this.restoreStatus = "configuration_changed";
      else if (!Array.isArray(state.groups) || state.groups.some(name => typeof name !== "string" || !used.has(name))) this.restoreStatus = "invalid_state";
      else { this.loaded = new Set(state.groups); this.restoreStatus = "restored"; }
    }
  }

  get groups(): string[] { return this.config.groups.filter(group => this.loaded.has(group.name)).map(group => group.name); }
  get active(): string[] {
    return [...new Set([...this.config.initial, LOAD_TOOLS, ...this.config.groups.filter(group => this.loaded.has(group.name)).flatMap(group => group.tools)])];
  }
  get state(): StoredState { return { fingerprint: this.fingerprint, groups: this.groups }; }
  guide(): string {
    return ["## 按需工具", LOAD_TOOLS_GUIDE,
      `当前已加载组：${this.groups.join("、") || "无"}。当前工具清单以本系统说明和真实工具定义为准。`,
      ...this.config.groups.map(group => `- ${group.name}（${this.loaded.has(group.name) ? "已加载" : "可加载"}）：${group.description}`),
      ...this.config.groups.filter(group => this.loaded.has(group.name) && group.guide).map(group => `### ${group.name}\n${group.guide}`),
    ].join("\n");
  }
  assertActive(actual: string[]): void {
    if ([...actual].sort().join(",") !== [...this.active].sort().join(",")) throw new Error("active tool confinement check failed");
    if (actual.some(name => name !== LOAD_TOOLS && !this.allowed.includes(name))) throw new Error("active tools exceed runner allowlist");
  }
  assertInvocation(name: string, batch: string[]): void {
    if (batch.includes(LOAD_TOOLS) && batch.length !== 1) throw new Error("load_tools 必须单独调用；等加载完成后，在下一轮使用新工具。此次并列调用未执行。");
    if (!this.active.includes(name)) throw new Error(`${name} 尚未加载；先单独调用 load_tools 加载对应能力组。`);
  }
  load(requested: string[], apply: () => void, persist: (state: StoredState) => void): { loaded: string[]; already_loaded: string[]; active_tools: string[] } {
    // Validate the entire request before touching active tools or persisted state.
    if (!requested.length || requested.some(name => !this.config.groups.some(group => group.name === name))) throw new Error("未知或空能力组；请使用按需工具目录中的组名。状态未改变。");
    const unique = [...new Set(requested)], previous = new Set(this.loaded);
    const added = unique.filter(name => !this.loaded.has(name));
    if (added.length) {
      for (const name of added) this.loaded.add(name);
      try { apply(); persist(this.state); }
      catch (error) { this.loaded = previous; apply(); throw error; }
    }
    return { loaded: added, already_loaded: unique.filter(name => previous.has(name)), active_tools: this.active };
  }
}

export function loadingTool(loading: ToolLoading, apply: () => void, persist: (state: StoredState) => void) {
  return defineTool({
    name: LOAD_TOOLS,
    label: "加载工具组",
    description: LOAD_TOOLS_GUIDE + " 可用组：" + loading.config.groups.map(group => `${group.name}（${group.description}）`).join("；"),
    parameters: Type.Object({ groups: Type.Array(Type.String(), { minItems: 1, description: "需要加载的能力组名称" }) }),
    executionMode: "sequential",
    execute: async (_id, params) => {
      const result = loading.load(params.groups, apply, persist);
      return { content: [{ type: "text" as const, text: JSON.stringify({ ...result, next_action: "加载完成；下一轮可使用新工具。同一稿件仍逐笔等待新 revision。" }) }], details: result };
    },
  });
}
