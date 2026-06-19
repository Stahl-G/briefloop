# MABW-080 / BriefLoop-090 Experiment Protocol

Read this for experiment cases, condition workspaces, scorecards, blind packs,
assessments, and summaries.

BriefLoop-090 is an experiment/readiness label. It is not the semver release
number. MABW-080 remains the experiment command namespace.

## Current Command Loop

```bash
multi-agent-brief experiments 080 validate-case <case_dir>
multi-agent-brief experiments 080 scaffold-condition --case <case_dir> --condition <baseline|memory|prompt_only> --workspace <workspace> --archive <archive>
multi-agent-brief experiments 080 register-run --case <case_dir> --workspace <workspace> --condition <condition> --output <run_record.json>
multi-agent-brief experiments 080 score-run --case <case_dir> --run-record <run_record.json> --output <scorecard.json>
multi-agent-brief experiments 080 export-blind-pack --case <case_dir> --scorecard <baseline_scorecard.json> --scorecard <memory_scorecard.json> --scorecard <prompt_only_scorecard.json> --output <blind_pack_dir>
multi-agent-brief experiments 080 import-assessment --assessment <blind_assessment.json> --blind-pack <blind_pack_dir>/blind_pack.json --reveal-mapping <blind_pack_dir>/reveal_mapping.json --output <assessed_scorecard.json>
multi-agent-brief experiments 080 summarize --case <case_dir> --scorecard <assessed_scorecard.json> --output <summary.json>
```

## Assessment Targets

- `delivery_brief`: full reader-delivery target. It requires finalize,
  reader-clean, archive, clean/reference-eligible run, and imported assessment.
- `auditable_brief`: content-level target for memory manifestation in
  `output/intermediate/audited_brief.md`. It stops at frozen audited brief,
  audit report, auditor gate pass, clean/reference-eligible run, same frozen
  fact layer, treatment isolation, blind assessment checks, and imported
  assessment. It is not management-ready delivery.

When `auditable_brief` is complete, do not run finalize or delivery for that
condition. Register, score, export blind assessment artifacts, import external
assessment, and summarize.

## Treatment Isolation

- Baseline must not see guidance text, expected manifestation text, memory
  snapshot, or prompt-only block.
- Memory may see guidance only through the approved Improvement Memory snapshot.
- Prompt-only may see guidance only through the explicit prompt guidance block.
- Python checks visibility and hashes; it does not judge semantic leakage by
  paraphrase.

## Blind Assessment Boundary

Formal summaries require condition-blind, hash-bound assessment artifacts. A
scorecard carrying copied or hand-edited blind metadata is not enough. Use the
export/import commands so blind item IDs, audited-brief hashes, scorecard
hashes, condition identity, run IDs, and guidance entry IDs are bound.

## Public Claim Boundary

Use "observed", "imported assessment", "same frozen fact layer", and
"formal/interpretable denominator" carefully. Do not say the experiment proves
Improvement Memory improves output quality unless fresh A-controlled evidence
actually supports that exact claim.
