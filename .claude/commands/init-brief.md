# Initialize Brief Workspace

Initialize a multi-agent-brief workspace using conversational onboarding.

Rules:

1. Ask the user at most 4 plain-language business questions.
2. Do not use AskUserQuestion for required free-text fields.
3. Do not ask the user to edit YAML, JSON, schema, or CLI flags.
4. If the user says "unknown", "default", or "choose for me", choose defaults.
5. Create `onboarding.json`.
6. Run:

```bash
multi-agent-brief init --from-onboarding onboarding.json
```

Show only:

* plain-language setup summary
* created workspace path
* created files
* next command
