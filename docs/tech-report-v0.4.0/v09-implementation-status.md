# v0.9 Support-Sufficiency Surfaces — Implementation Status (v0.11.12)

**Purpose**: Map v0.9 design surfaces to current code status. Authoritative detail lives in
`docs/architecture-status.md` and `docs/support-matrix.md`.

**Code snapshot**: v0.11.12 (`65b384c06bccffbb183a76db1260def02853b951`) | **Report**: Architecture Reference v0.4.0 | **Date**: 2026-07-09

---

## Summary

| Surface | v0.3.0 report said | v0.11.12 status | Blocks delivery by default? |
|---------|-------------------|-----------------|---------------------------|
| Atomic Claim Graph | Planned v0.9.0 | **Experimental** | No |
| Evidence Span Registry | Planned v0.9.1 | **Experimental** | No |
| Claim-Support Matrix | Planned v0.9.2 | **Experimental** | No |
| Semantic Assessment Report | Planned v0.9.x | **Experimental** (proposal-only) | No |
| Human adjudication ledger | Planned | **Experimental** (`semantic-support adjudicate`) | No |
| Durable source evidence pack | Not in v0.3.0 §8 | **Experimental** (`sources materialize-pack`) | No |
| Coverage / omission continuity | Planned coverage gate | **Supported** (deterministic gate finding; not full recall) | Stage-scoped |
| Trajectory Regulation | Planned v0.8 | **Supported** (decision narrowing) | When budgets exhausted |
| Guidance manifestation | Planned v0.8.5 | **Experimental** (read-only projection) | No |
| Issue Candidate System | Boundary only (Appendix H) | **Not shipped** | No public field/schema draft |
| Release eligibility scorecard | Planned v0.9.x | **Not shipped** | — |
| Same-evidence regression harness | Planned | **Partial** (eval-case only; not release authority) | — |

---

## Experimental boundaries (do not overclaim)

1. **Missing optional artifacts remain non-blocking.** Invalid present artifacts are not consumed for support projection.
2. **Semantic assessment is proposal-only** until humans adjudicate; adjudication records do not auto-write Claim-Support Matrix rows.
3. **`extract` for evidence_extract** seeds UTF-8 text spans only; PDF/binary parsing is not shipped.
4. **Quality Panel** summarizes existing control surfaces; it is not a quality score or gate replacement.

---

## CLI entrypoints (representative)

```bash
multi-agent-brief state check --workspace <ws>
multi-agent-brief gates check --workspace <ws>
multi-agent-brief semantic-support bind --workspace <ws>    # when report present
multi-agent-brief semantic-support adjudicate --workspace <ws> \
  --proposal-id <proposal-id> --decision accept --reason "<human rationale>"
multi-agent-brief quality summarize --workspace <ws>
multi-agent-brief sources materialize-pack --config <ws>/config.yaml
multi-agent-brief extract --workspace <ws> --scope <text> --source <file>
```

---

## Public wording

- Say: *BriefLoop operationalizes traceability and experimental support-sufficiency records.*
- Do not say: *BriefLoop proves truth*, *v0.9 is fully delivered*, or *experimental surfaces authorize publication.*
