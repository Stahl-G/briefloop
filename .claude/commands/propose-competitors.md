---
description: Propose competitor candidates for a workspace based on user.md context
argument-hint: "<workspace-path>"
---

You are recommending competitor candidates for workspace: $ARGUMENTS

Follow this sequence:

1. Read context files:
   - $ARGUMENTS/user.md
   - $ARGUMENTS/config.yaml
   - $ARGUMENTS/competitor_universe.yaml (target entity and any existing entities)

2. Call the **market-competitor-planner** subagent:
   - Provide user.md context (company, industry, market_scope, focus_areas).
   - Ask it to recommend 3-8 competitor entities.
   - Each candidate must include: entity_id, name, aliases, relation,
     relevance_reason, market_overlap (geography, product, value_chain),
     confidence, and approved: false.

3. Write $ARGUMENTS/competitor_candidates.yaml:
   - Merge existing approved-but-not-merged candidates (if any).
   - Add new recommendations from the planner.
   - Do NOT set approved: true for any candidate.

4. Report:
   - How many candidates were added.
   - Which entities were recommended and why (briefly).
   - Remind user to review candidates, set `approved: true` on confirmed ones,
     then run `multi-agent-brief competitors merge --config $ARGUMENTS/config.yaml`.
   - Do not auto-approve or merge candidates.
