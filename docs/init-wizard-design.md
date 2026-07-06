# Init Wizard Design

The `multi-agent-brief init` command creates a reusable brief workspace for non-programmer users who want to build management briefs, weekly reports, industry briefs, and strategy notes.

## CLI Modes

Interactive workspace initialization:

```bash
multi-agent-brief init
multi-agent-brief init my-brief-workspace
```

Synthetic demo initialization:

```bash
multi-agent-brief init --demo
multi-agent-brief init demo-workspace --demo
```

Non-interactive initialization for AI agents and scripts must use conversational onboarding data:

```bash
multi-agent-brief init solar-weekly --from-onboarding onboarding.json
```

The CLI must not create a real workspace from hidden defaults in a non-interactive environment. Developer-only direct CLI initialization is allowed only when all required business fields are explicit.

## Prompt Order

Language selection is always first. The selected interface language controls later prompts and generated user-facing comments. Brief output language is stored separately.

Prompt order:

1. Language
2. Company
3. Role
4. Industry
5. Brief title
6. Audience
7. Focus areas
8. Reporting cadence
9. News selector settings
10. RAG settings
11. Output formats
12. Safety and audit settings

## Generated Workspace

```text
brief-workspace/
  config.yaml
  profile.yaml
  sources.yaml
  input/
    README.md
  output/
  .gitignore
```

The generated `.gitignore` excludes local outputs, RAG indexes, private inputs, private outputs, logs, Office documents, PDFs, and environment files.

## Language Config

New workspaces use:

```yaml
language:
  interface: "zh-CN"
  output: "zh-CN"
  source_handling: "preserve_original"
```

Backward compatibility is preserved for legacy config files that use:

```yaml
project:
  language: "en-US"
```

Compatibility rules:

- If `language.output` exists, use it as the brief language.
- Else if `project.language` exists, use it as the brief language.
- If `language.interface` is missing, default it to the brief language.
- If both are missing, default to `zh-CN`.

## Selector And RAG Defaults

Selector defaults:

```yaml
selector:
  enabled: true
  max_items: 20
  require_fresh_source: true
  topic_diversity: true
```

RAG is optional and disabled by default:

```yaml
retrieval:
  enabled: false
  provider: "ollama"
  model: "nomic-embed-text"
  chroma_dir: ".rag/chroma"
  top_k: 5
  lookback_days: 365
```

When `--retrieval-provider gemini` is selected, the generated model is `gemini-embedding-001`.

RAG must not write directly into the final brief. Every RAG-derived statement must enter the Claim Ledger before it can be cited in the brief.

## Non-Goals

- Do not implement full RAG in the init wizard.
- Do not implement real RSS, SEC, Feishu, Slack, or email connectors here.
- Do not add real company-specific examples.
- Do not require API keys merely to create a workspace. Live external web
  search uses Tavily by default and requires `TAVILY_API_KEY` before source
  discovery.
- Do not enable RAG by default.
- Do not allow generated private outputs to be committed by default.
