# MABW Onboarding JSON

Use chat answers to create `onboarding.json`.

## Example

```json
{
  "company_or_org": "阿特斯",
  "industry_or_theme": "光伏和储能",
  "task_objective": "美国光储行业简报",
  "audience": "management team",
  "language": "中文",
  "cadence": "weekly",
  "source_style": "reliable research",
  "output_style": "executive brief, conclusion-first",
  "must_watch": [],
  "forbidden_sources": [],
  "web_search_mode": "runtime_websearch"
}
```

## Field Notes

- Use `runtime_websearch` for market, policy, industry, news, and competitor briefs that need current public information.
- Use `local_only` when the user provides all sources in the workspace.
- Use `configure_later` when source access is undecided.
