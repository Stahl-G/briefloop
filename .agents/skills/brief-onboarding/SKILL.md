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

Ask at most 10 questions:

1. What should this brief monitor?
   Recommended default: company + industry + policy + competitors + risk events.

2. Who will read it?
   Recommended default: management / leadership team.

3. How broad should sources be?
   Recommended default: reliable public sources + industry media.

4. What language and cadence?
   Recommended default: English, weekly.

5. What specific focus areas are most important?
   Recommended default: based on industry (e.g., for automotive: sales data, autonomous driving, policy, supply chain, product launches).

6. Which search backend should be used for web search?
   Options: tavily (default, fast AI search), exa (deep research), brave (independent web index), firecrawl (search + scrape), serper (Google SERP), serpapi (broad SERP), none (local files only).
   Recommended default: tavily.

7. How many items should each brief contain?
   Recommended default: 8 items.

8. What is the maximum age for source materials (in days)?
   Recommended default: 14 days.

9. How strict should the audit be?
   Options: standard (default), strict (fail on any issue), lenient (allow minor issues).
   Recommended default: standard.

10. Are there any sources or topics that should be avoided?
    Recommended default: none.

Accept natural-language answers. If incomplete, infer defaults.

Then create `onboarding.json` with:
- target
- company_or_org
- industry_or_theme
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

Then run:

```bash
multi-agent-brief init --from-onboarding onboarding.json
```

Finally summarize:

* workspace created
* brief audience
* monitor scope
* source style
* search backend
* max items per brief
* source age limit
* audit strictness
* output style
* next command
