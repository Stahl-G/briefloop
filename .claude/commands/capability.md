---
description: Show Multiagent Brief capability board and setup status
argument-hint: "[workspace-path] [--info <capability-id>]"
---

Show the user the project capability board for: $ARGUMENTS

Rules:

1. Run `multi-agent-brief capability $ARGUMENTS` if arguments are provided; otherwise run `multi-agent-brief capability`.
2. If the user asks about search capability specifically, also run `multi-agent-brief capability --info web_search` or `multi-agent-brief capability <workspace> --info web_search`.
3. Explain supported web-search backends as: Tavily, Exa, Brave, Firecrawl, Serper, runtime_websearch, configure_later. Do not present Tavily as the only option.
4. Do not ask the user to paste API keys into chat. Tell them to use `.env` or shell environment variables.
5. Summarize only enabled, needing setup, and recommended next actions.
