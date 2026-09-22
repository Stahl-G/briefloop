// A ResourceLoader that deliberately discovers nothing.
//
// The default pi loader reads global ~/.pi/agent resources, ancestor
// AGENTS.md/CLAUDE.md files, project skills and extensions. BriefLoop sessions
// carry their own frozen contract: an inherited instruction file could silently
// widen a reviewer's authority, so this loader returns empty resource sets and
// a caller-supplied system prompt verbatim. Only explicitly supplied, bundled
// extensions are loaded; user/global/project extension discovery stays off.
import { createExtensionRuntime } from "@earendil-works/pi-coding-agent";
import type { ResourceLoader, Extension } from "@earendil-works/pi-coding-agent";

export function fixedLoader(systemPrompt: string, bundledExtensions: Extension[] = []): ResourceLoader {
  const runtime = createExtensionRuntime();
  return {
    getExtensions: () => ({ extensions: bundledExtensions, errors: [], runtime }),
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
