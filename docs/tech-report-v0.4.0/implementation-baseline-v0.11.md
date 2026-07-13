# Implementation Baseline — v0.11.12 (Tech Report v0.4.0 Sync)

**Supersedes**: `briefloop-architecture-reference-v0.3.0.md` §8 (v0.8.3 snapshot)
**Immutable tag**: `v0.11.12` (`65b384c06bccffbb183a76db1260def02853b951`)

---

## Version line (high level)

```text
v0.6.x  runtime state, gates, feedback, provenance, audience, switchboard
v0.7.x  improvement ledger, reader-final gate, completion transactions, solar B+ ref run
v0.8.x  claim freeze, fast-rerun, run integrity, archived `MABW-080` experiments
v0.9.x  support-sufficiency experimental stack (graph / spans / matrix / semantic proposals)
v0.10.x finalize transaction promotion, Claude five-verb writer path, bundle manifests
v0.11.0 product baseline (briefloop new × 3), ReportSpec, PolicyProfile, Quality Panel
v0.11.12 operator runtime, source-clone WorkBuddy Skill bundle, semantic adjudicate
post-tag v1.0 tracking: docs/v1-pilot-evidence.md (currently not_satisfied)
```

---

## Supported product surfaces (v0.11.0)

| User command | Internal pack | Notes |
|--------------|---------------|-------|
| `briefloop new industry-weekly` | `market_weekly` | Local-first skeleton |
| `briefloop new management-monthly` | `management_monthly` | Local-first skeleton |
| `briefloop new document-review` | `evidence_extract` | Extraction workspace |

Writer path: `new` → `run` → `status` → `feedback` → `deliver` (Claude `/briefloop`).

---

## Measurement & CI

- **25** packaged `eval-cases` (public-safe)
- **2767+** pytest tests, zero LLM in CI
- BriefLoop-090 (archived experiment ID `MABW-080`): **archived experimental** (not first-screen onboarding)

---

## Reference runs added since v0.3.0 report

| Run | Doc | Grade / role |
|-----|-----|----------------|
| BriefLoop-090 synthetic pilot | `docs/reference-runs/briefloop-090-a-controlled-pilot.md` | Narrow memory-pattern observation |
| v0.11.3 Product OS package | `docs/reference-runs/v0.11.3-product-os-reader-quality-reference.md` | Deterministic reader/audit bundle demo |
| Post-snapshot v1.0 evidence tracking | `docs/v1-pilot-evidence.md` | Added after v0.11.12; current release bookkeeping remains `not_satisfied` |

---

## Sources of truth when docs disagree

1. `docs/support-matrix.md` — capability status
2. `docs/architecture-status.md` — implemented vs roadmap
3. `VERSION` + `CHANGELOG.md` — release facts
4. This tech report — architecture narrative (must stay aligned with 1–3)
