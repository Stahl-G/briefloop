---
name: brief-onboarding
description: Use when the user wants to initialize, start, configure, or set up a multi-agent-brief workspace. Interview the user in plain language and convert answers into onboarding.json.
---

# Brief Onboarding

You are onboarding a non-programmer user.

Do not expose:
- YAML
- JSON
- schema
- CLI flags
- source_profile
- selector_max_items
- retrieval_provider
- output_formats

Ask at most 14 questions:

0. What should this brief be called?
   Required field. Examples: "Canadian Solar Photovoltaic Weekly", "阿特斯光伏行业周报", "Global Macro Strategy Monthly".
   Recommended default: "{Company} {Industry} Weekly" (auto-generated if skipped).

1. What is your company or organization name?
   Required field. Do not use defaults.

2. What is your role or department?
   Examples: Strategy, Research, Marketing, Investor Relations, Policy, Management.
   Recommended default: Strategy.

3. What should this brief monitor?
   Recommended default: company + industry + policy + competitors + risk events.

4. Do you want to enable competitor monitoring?
   If yes, ask which specific competitors to track (company names).
   Examples: "Yes — track Acme Corp and Globex Inc" or "No, not now".
   Recommended default: yes if the user mentioned competitors in question 3.

5. Who will read it?
   Recommended default: management / leadership team / marketing / investment team / research.

6. How broad should sources be?
   Recommended default: reliable public sources + industry media.

7. What language and cadence?
   Recommended default: Chinese, weekly.

8. What specific focus areas are most important?
   Recommended default: based on industry (e.g., for automotive: sales data, AI, policy, supply chain, product launches).

9. Enable live web search?
   Options: yes (then select from available backends), no (local files only).
   If yes, show configured backends (based on API keys in .env) plus runtime-provided web search option.
   Recommended default: configure later.

10. How many items should each brief contain?
    Recommended default: 20 items.

11. What is the maximum age for source materials (in days)?
    Recommended default: 14 days.

12. How strict should the audit be?
    Options: standard (default), strict (fail on any issue), lenient (allow minor issues).
    Recommended default: standard.

13. Are there any sources or topics that should be avoided?
    Recommended default: none.

Accept natural-language answers. If incomplete, infer defaults.

Then create `onboarding.json` with:
- target
- brief_title
- company_or_org
- industry_or_theme
- role_plain
- audience_plain
- source_style_plain
- output_style_plain
- language_plain
- cadence_plain
- must_watch
- focus_areas_plain
- search_backend_plain
- max_items_per_brief
- source_age_days
- audit_strictness
- forbidden_sources
- competitor_preferences (object with `enabled: true/false` and `names: [list of competitor names]`)

Then run:

```bash
multi-agent-brief init --from-onboarding onboarding.json
```

Finally summarize:

* brief title (user-specified or auto-generated)
* workspace created
* brief audience
* monitor scope
* competitor monitoring status (enabled/disabled, which competitors)
* source style
* search backend
* max items per brief
* source age limit
* audit strictness
* output style
* next command
