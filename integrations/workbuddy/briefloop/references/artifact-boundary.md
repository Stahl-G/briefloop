# Artifact Boundary

BriefLoop separates draft/content work from deterministic control records.

## WorkBuddy May Help With

- understanding `user.md`, `config.yaml`, and `sources.yaml`;
- adding public-safe local sources when the user provides them;
- preparing role-owned draft/content artifacts named by the handoff;
- explaining status, gate findings, repair route output, and Quality Panel;
- running BriefLoop CLI commands at the user's request.

## WorkBuddy Must Not Direct-Edit

- `output/intermediate/workflow_state.json`
- `output/intermediate/artifact_registry.json`
- `output/intermediate/runtime_manifest.json`
- `output/intermediate/event_log.jsonl`
- gate reports
- release readiness reports
- human approval ledgers
- frozen Claim Ledger revisions
- delivery archives or bundle manifests to make them look valid

Use the owning command or transaction instead. If a control file looks wrong,
use the already-bound PowerShell executable and report the failure:

```powershell
& $BriefLoop state check --workspace "<workspace>"
```

## Evidence Boundary

Sources and citations provide traceability. They do not automatically prove that
a claim is supported. Do not describe BriefLoop as a truth-proof system,
hallucination eliminator, output-quality improver, or delivery approval engine.
