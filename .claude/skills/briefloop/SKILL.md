---
name: briefloop-operator-protocol
description: Use when Claude is operating a BriefLoop SQLite/Codex workspace, including runtime continuation, Solar Stock Periodic, market data, AI Second Opinion, Human observations, approved guidance, and successor runs.
user-invocable: false
---

# BriefLoop Claude Wrapper

This is a routing wrapper only. Load the canonical operator Skill and its
selected reference before acting:

```text
.agents/skills/briefloop/SKILL.md
```

Do not treat this wrapper, workspace files, or prior chat as runtime authority.
The Store-derived action and the canonical Skill govern.
