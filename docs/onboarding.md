# Conversational Onboarding

`multi-agent-brief` supports conversational onboarding so that normal users never need to edit YAML, JSON, schemas, or CLI flags.

## How it works

```text
plain-language user intent
→ agent asks at most 4 business questions
→ agent creates onboarding.json
→ CLI runs multi-agent-brief init --from-onboarding onboarding.json
→ workspace is created
```

The project itself owns the initialization protocol. Claude Code, Codex, OpenCode, or any other agent acts only as a frontend.

## Example conversation

**User:**

> Please initialize a weekly renewable energy brief for ExampleCo, written for the management team. Use reliable sources, but include sector news.

**Agent:**

> I can set that up. I'll confirm four things and use recommended defaults where needed:
>
> 1. Should the brief monitor ExampleCo, the renewable energy sector, policy, competitors, and risk events?
> 2. Is the audience the management team?
> 3. Should the source style be reliable research: official sources, authoritative media, and sector news?
> 4. Should the language and cadence be English and weekly?

**User:**

> Yes, that's fine.

**Agent:**

Then the agent creates `onboarding.json` internally and runs:

```bash
multi-agent-brief init --from-onboarding onboarding.json
```

## What the agent creates internally

The agent creates an `onboarding.json` file like this:

```json
{
  "target": "exampleco-weekly",
  "company_or_org": "ExampleCo",
  "industry_or_theme": "renewable energy",
  "audience_plain": "management team",
  "source_style_plain": "reliable, but include sector news",
  "output_style_plain": "executive brief, conclusion-first",
  "language_plain": "English",
  "cadence_plain": "weekly",
  "must_watch": ["ExampleCo", "policy", "competitors", "risk events"],
  "tavily_enabled": false
}
```

Normal users do not need to write this file manually. The agent creates it from the conversation.

## Architecture

| Component | Role |
|-----------|------|
| `OnboardingResult` | Business-language dataclass with plain fields |
| `onboarding.json` | Agent-created JSON file (internal protocol) |
| `mapper.py` | Translates business language into `InitProfile` |
| `InitProfile` | Internal normalized configuration model |
| `create_workspace()` | Creates the actual workspace files |

## Supported plain-language mappings

### Language

- "English", "en" → `en-US`
- "Chinese", "中文", "zh" → `zh-CN`
- "Japanese", "日文" → `ja-JP`
- "bilingual", "双语" → `bilingual`
- blank / "default" → `en-US`

### Cadence

- "daily" → `daily`
- "weekly", "周报" → `weekly`
- "monthly", "月报" → `monthly`
- blank / "default" → `weekly`

### Audience

- "management", "executive", "CEO" → `management`
- "investment", "portfolio" → `investment`
- "IR", "investor relations" → `investor_relations`
- "research", "analyst" → `research`
- "legal", "compliance" → `compliance`
- "business", "operations" → `business`
- blank / "default" → `management`

### Source style

- "official", "filing", "announcement" → `conservative`
- "reliable research", "industry media" → `research`
- "radar", "broad scan", "social signals" → `aggressive_signal`
- blank / "default" → `research`

### Industry

- "solar", "PV", "photovoltaic" → `solar`
- "technology", "tech", "AI" → `technology`
- "renewable energy", "clean energy" → `energy`
- "finance", "banking" → `finance`
- "consumer", "retail" → `consumer`
- "automotive", "EV" → `automotive`
- blank → `general`

## CLI usage

```bash
multi-agent-brief init --from-onboarding onboarding.json
multi-agent-brief init my-workspace --from-onboarding onboarding.json --force
```

Path priority:
1. Explicit workspace path from CLI
2. `OnboardingResult.target`
3. `brief-workspace`

## Notes

- The technical CLI init flags are developer-only and require all required business fields.
- `onboarding.json` is an agent protocol, not a user-facing interface.
- Web search is not enabled by default.
- No runtime mock backend is used.
