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

Ask at most 4 questions:

1. What should this brief monitor?
   Recommended default: company + industry + policy + competitors + risk events.

2. Who will read it?
   Recommended default: management / leadership team.

3. How broad should sources be?
   Recommended default: reliable public sources + industry media.

4. What language and cadence?
   Recommended default: English, weekly.

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

Then run:

```bash
multi-agent-brief init --from-onboarding onboarding.json
```

Finally summarize:

* workspace created
* brief audience
* monitor scope
* source style
* output style
* next command
