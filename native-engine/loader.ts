// A ResourceLoader that deliberately discovers nothing.
//
// The default pi loader reads global ~/.pi/agent resources, ancestor
// AGENTS.md/CLAUDE.md files, project skills and extensions. BriefLoop sessions
// carry their own frozen contract: an inherited instruction file could silently
// widen a reviewer's authority, so this loader returns empty resource sets and
// a caller-supplied system prompt verbatim.
import { createExtensionRuntime } from "@earendil-works/pi-coding-agent";
import type { ResourceLoader } from "@earendil-works/pi-coding-agent";

export function fixedLoader(systemPrompt: string): ResourceLoader {
  return {
    getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
    getSkills: () => ({ skills: [], diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => systemPrompt,
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [],
    getAppendSystemPromptSources: () => [],
    extendResources: () => {},
    reload: async () => {},
  };
}
