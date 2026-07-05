# Initialize Brief Workspace

Initialize a briefloop workspace using conversational onboarding.

Rules:

1. Ask the user at most 4 plain-language business questions.
2. Do not use AskUserQuestion for required free-text fields.
3. Do not ask the user to edit YAML, JSON, schema, or CLI flags.
4. Never ask the user to paste API keys into chat. For live search, explain all options: Tavily, Exa, Brave, Firecrawl, Serper, runtime_websearch, or configure_later. If an API backend is chosen, tell the user to set the key in `.env` or shell.
5. Do not use hidden defaults for required business fields. If the user says "unknown", "default", or "choose for me" for optional fields, choose safe defaults, but keep company, industry/theme, task/title, audience, language, cadence, source style, output style explicit.
6. Create `onboarding.json` in the current working directory. If writing there fails, create `.mabw-onboarding/onboarding.json` and pass that exact path to the CLI.
7. Run:

```bash
briefloop init <workspace-path> --from-onboarding onboarding.json
```

Show only:

* plain-language setup summary
* created workspace path
* created files
* next command
