# .agents Directory

This directory stores runtime skill contracts and Hermes runtime skills.

In repository development mode, treat files under this directory as source assets to inspect, edit, and test. Their instructions become active only when a runtime explicitly selects that skill or uses the Hermes skill.

`skills/*/SKILL.md` files are capability contracts, not platform-specific subagent definitions.

Platform-specific subagents live in:

- `.codebuddy/agents/briefloop-*.md` for CodeBuddy project sub-agents, hand-maintained and source-clone-only
- `.claude/agents/*.md` for Claude Code
- `.opencode/agents/*.md` for OpenCode
- `.codex/agents/*.toml` for Codex

Hermes child tasks are created through `delegate_task` from `.agents/hermes-skills/`.

When editing skills, keep each `SKILL.md` focused on:

- when to use the skill
- inputs
- outputs
- work steps
- handoff target

Follow Claude Skills progressive disclosure: keep `SKILL.md` concise, use specific frontmatter descriptions for routing, and move long templates or reference material into `references/`.
