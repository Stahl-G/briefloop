# Runtime Recipes

Runtime recipes describe how an external Orchestrator may assign existing MABW
stages to runtime agents or humans. They are not Python workflows and they do
not add new artifact contracts.

Every recipe starts after:

```bash
briefloop run --workspace <workspace> --runtime operator
```

The run command creates the runtime handoff, state files, audience snapshot, and
control switchboard. A recipe may compress role assignment, but it must preserve
the required accountable artifacts.

## Full Subagent Workflow

Use when the selected runtime can delegate specialist roles.

```text
doctor
→ source discovery when configured
→ input governance when available
→ scout
→ screener
→ claim-ledger
→ analyst
→ editor
→ auditor
→ gates/state review
→ finalize
```

Expected runtime roles:

- scout
- screener
- claim-ledger
- analyst
- editor
- auditor
- formatter

Python remains the tool, validator, audit, control, and rendering layer. The
runtime Orchestrator decides when to delegate roles and when to run CLI tools.

## CodeBuddy Runtime Handoff

Use `--runtime codebuddy` when operating from a source checkout with the
project Skill and role-agent assets available:

```text
.codebuddy/skills/briefloop/
.codebuddy/agents/briefloop-*.md
```

The main CodeBuddy session remains the Orchestrator. It loads the BriefLoop
project Skill, invokes role sub-agents explicitly, and runs deterministic
BriefLoop CLI transactions after each role returns. The Skill must not use
`context: fork`, because CodeBuddy sub-agents cannot spawn other sub-agents.

Runtime capabilities:

```text
delegation_supported: true
nested_subagents_supported: false
role_agents_run_cli_transactions: false
```

Not allowed:

- Do not let role sub-agents run `briefloop` CLI transactions.
- Do not let role sub-agents edit control files, gate reports, delivery files,
  release reports, or frozen artifacts.
- Do not claim a role sub-agent ran unless CodeBuddy actually invoked it.
- Do not treat CodeBuddy handoff as gate authority, delivery approval, release
  authority, semantic proof, or output-quality proof.

## Operator Runtime Compact Workflow

Use `--runtime operator` when the host does not have a dedicated BriefLoop
runtime adapter such as Hermes, Claude Code, Codex, OpenCode, or CodeBuddy. This is a
host-agnostic compact operator workflow. It does not assume subagent or delegate
capability. Historical `auto` / `manual` / implicit `controls` manifests are readable diagnostics,
but execution resumes only after an explicit reset into a canonical runtime.

The compact workflow compresses role assignment:

```text
run handoff
→ one operator prepares candidate/screened claims
→ same operator creates claim_ledger.json through the deterministic freeze transaction
→ same operator drafts/edits audited_brief.md with [src:<claim_id>]
→ audit/gates/state review
→ finalize
```

Required invariant:

```text
same required artifacts, fewer runtime roles
```

Minimum accountable artifacts:

```text
output/intermediate/candidate_claims.json
output/intermediate/screened_candidates.json
output/intermediate/claim_ledger.json
output/intermediate/audited_brief.md
output/intermediate/audit_report.json
output/delivery/brief.md
output/delivery/<named>.docx when DOCX output is configured
output/source_appendix.md as an audit/control copy when configured
```

Allowed compression:

- One operator may perform scout + screener + claim-ledger preparation.
- One operator may perform analyst + editor drafting.
- If the host provides a real child-agent/delegate tool, the operator may
  delegate the named role.
- A human may assist with extracting claims or editing the audited brief.
- Python may validate, audit, gate-check, and finalize.

Not allowed:

- Do not skip `run` or handoff.
- Do not skip the Claim Ledger.
- Do not write the final reader brief directly from input files.
- Do not treat feedback as evidence.
- Do not treat `audience_profile.md` as source evidence.
- Do not finalize without audit, gates, and state review.
- Do not let Python execute scout, analyst, or editor behavior.
- Do not claim subagents ran unless the host actually provided delegation.
- Do not claim compact workflow is quality-equivalent to full specialist
  delegation.

## Retired Fast-Rerun Recipe

The former `state import-fact-layer` and `run --recipe fast-rerun` path was
deleted with the legacy runtime-state stack in LD2-3. It is not a current
SQLite recipe, successor mechanism, experiment path, or fallback. Historical
command semantics remain only in git history, archived experiment material,
and frozen reference-run records.

For a current fresh SQLite workspace, start a normal successor only through the
explicit Human `briefloop runtime successor-start` transaction. That command
does not import a prior fact layer: the new run must establish its own current
direction, sources, evidence, Claim Ledger, gates, finalize, and delivery truth.
Optional approved-guidance reuse is presentation context for Analyst/Editor
only, not evidence reuse or a fast path.

## Existing Draft Review

Existing-draft review is not a standalone v0.7.0 mode. Treat it as a compact
workflow variant after `run` has created runtime state and handoff artifacts.
The draft may be placed under `input/context/` as reference context or converted
into auditable artifacts by the runtime/human process, but it must not bypass
Claim Ledger, audit, gates, or finalize boundaries.
