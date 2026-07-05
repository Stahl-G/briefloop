---
description: Show Multiagent Brief capability board and setup status
argument-hint: "[workspace-path] [--info <capability-id>]"
---

Show the user the project capability board for: $ARGUMENTS

Rules:

1. Run `briefloop capability $ARGUMENTS` if arguments are provided; otherwise run `briefloop capability`.
2. If the user asks about search capability specifically, also run `briefloop capability --info web_search` or `briefloop capability <workspace> --info web_search`.
3. Explain supported web-search backends as: Tavily, Exa, Brave, Firecrawl, Serper, runtime_websearch, configure_later. Do not present Tavily as the only option.
4. Do not ask the user to paste API keys into chat. Tell them to use `.env` or shell environment variables.
5. Summarize only enabled, needing setup, and recommended next actions.
