// Tools the runner (BriefLoop's Python side) declares and executes.
//
// A role's work is mostly BriefLoop operations: render a PDF page, save a
// URL, check a draft, register a figure, admit a result. Re-implementing each
// one here would fork BriefLoop's own logic into a second codebase. Instead the
// runner sends each tool's name, description and JSON Schema at session_create;
// the engine registers it with pi and, when the model calls it, forwards the
// arguments over the wire (event `tool_request`) and waits for `tool_result`.
// Adding a role or a tool changes the runner's registry, not this engine.
//
// A tool marked `settles` ends the run when the runner accepts its call: its
// accepted value becomes the execution's result, the way submit_review does.
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";

type TextContent = { type: "text"; text: string };
type ImageContent = { type: "image"; data: string; mimeType: string };

export interface RunnerToolSpec {
  name: string;
  label?: string;
  description: string;
  // One line for the system prompt's tool guide.
  guide?: string;
  // JSON Schema of the arguments object.
  parameters: Record<string, unknown>;
  settles?: boolean;
  // Calls that change state or end the run never race another call.
  sequential?: boolean;
}

export interface RunnerResult {
  ok: boolean;
  error?: string;
  content?: Array<TextContent | ImageContent>;
  // For a settling tool: the accepted result, as JSON text.
  settle?: string;
}

export interface RunnerHooks {
  call(tool: string, args: unknown): Promise<RunnerResult>;
  acceptsImages(): boolean;
  settle(value: string): void;
}

const NAME = /^[a-z][a-z0-9_]{1,40}$/;

export function parseRunnerTools(value: unknown, reserved: string[]): RunnerToolSpec[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new Error("runner_tools must be a list");
  const seen = new Set(reserved);
  return value.map((raw) => {
    const spec = raw as RunnerToolSpec;
    if (!spec || typeof spec.name !== "string" || !NAME.test(spec.name)) throw new Error(`runner tool name invalid: ${JSON.stringify(spec?.name)}`);
    if (seen.has(spec.name)) throw new Error(`runner tool name taken: ${spec.name}`);
    seen.add(spec.name);
    if (typeof spec.description !== "string" || !spec.description.trim()) throw new Error(`runner tool ${spec.name}: description required`);
    const params = spec.parameters as Record<string, unknown>;
    if (!params || typeof params !== "object" || params.type !== "object") throw new Error(`runner tool ${spec.name}: parameters must be an object schema`);
    return spec;
  });
}

export function runnerTools(specs: RunnerToolSpec[], hooks: RunnerHooks) {
  return specs.map((spec) => defineTool({
    name: spec.name,
    label: spec.label ?? spec.name,
    description: spec.description,
    parameters: Type.Unsafe<Record<string, unknown>>(spec.parameters),
    ...(spec.sequential || spec.settles ? { executionMode: "sequential" as const } : {}),
    execute: async (_id, params) => {
      const result = await hooks.call(spec.name, params);
      if (!result.ok) throw new Error(result.error || `${spec.name} 执行失败`);
      const images = hooks.acceptsImages();
      const content: Array<TextContent | ImageContent> = [];
      let dropped = 0;
      for (const item of result.content ?? []) {
        if (item.type === "image") { if (images) content.push(item); else dropped += 1; }
        else if (item.type === "text") content.push(item);
      }
      if (dropped) content.push({ type: "text", text: `（${dropped} 张图片未发送：当前模型不接收图像输入，不能声称已目视核对。）` });
      if (!content.length) content.push({ type: "text", text: "完成。" });
      if (spec.settles && result.settle !== undefined) {
        hooks.settle(result.settle);
        return { content, details: { settled: true }, terminate: true };
      }
      return { content, details: { settled: false } };
    },
  }));
}
