# BriefLoop 架构参考 v0.4.0：修订路线图

**Revision Round**: 3 (v0.4.0 research-framing update)
**Date**: 2026-07-10
**Previous Version**: v0.3.0 (code snapshot v0.8.3)
**Target Version**: v0.4.0 (code snapshot v0.11.12 at `65b384c06bccffbb183a76db1260def02853b951`, approaching v1.0.0)
**Revision Type**: Major — implementation baseline sync + Product OS + v0.9 experimental status

---

## Revision Tracking Table

| # | Issue | Type | Section | Priority | Status |
|---|-------|------|---------|----------|--------|
| 1 | Sync §8 to v0.11.12 | Major | §8 | P1 | DONE |
| 2 | v0.9 Experimental vs Planned table | Major | §3.6, §11.3 | P1 | DONE |
| 3 | Product OS / briefloop new baseline | Major | §3.6, §8 | P1 | DONE |
| 4 | Abstract refresh | Major | 摘要 | P1 | DONE |
| 5 | Reference runs §9.5 | Minor | §9 | P2 | DONE |
| 6 | Eval/test counts | Minor | §8, App E | P2 | DONE |
| 7 | Role topology + Delivery Editor | Minor | App D | P2 | DONE |
| 8 | Rename timeline | Minor | App I | P2 | DONE |
| 9 | Trajectory regulation correction | Minor | §10.1 | P2 | DONE |
| 10 | Guidance manifestation status | Minor | §7.3 | P2 | DONE |
| 11 | docs/README index | Minor | docs | P2 | DONE |
| 12 | Response to reviewers | Process | tech-report-v0.4.0/ | P2 | DONE |
| 13 | Red-team pass on v0.4.0 overclaims | Major | 全文 | P1 | PENDING |
| 14 | Bilingual abstract for external paper export | Minor | — | P3 | DEFERRED |
| 15 | Integrate Weng harness-engineering synthesis with chronology and evidence-tier boundary | Major | §1.4, §7.5, §10.1, §11, App G | P1 | DONE |
| 16 | Retire the previous project name from current report prose and naming policy | Major | 全文、命名政策 | P1 | DONE |
| 17 | Rewrite the Chinese report for terminology, readability, and claim discipline | Major | 全文 | P1 | DONE |
| 18 | Add a three-lane architecture figure and self-contained HTML reading edition | Major | §3、HTML | P1 | DONE |
| 19 | Publish a full English manuscript, architecture figure, and self-contained HTML edition | Major | 全文、§3、HTML | P1 | DONE |

---

## Deliverables

- [x] `docs/briefloop-architecture-reference-v0.4.0.md`
- [x] `docs/briefloop-architecture-reference-v0.4.0.html`
- [x] `docs/assets/briefloop-architecture-v0.4.0.svg`
- [x] `docs/briefloop-architecture-reference-v0.4.0.en.md`
- [x] `docs/briefloop-architecture-reference-v0.4.0.en.html`
- [x] `docs/assets/briefloop-architecture-v0.4.0.en.svg`
- [x] `docs/tech-report-v0.4.0/implementation-baseline-v0.11.md`
- [x] `docs/tech-report-v0.4.0/v09-implementation-status.md`
- [x] `docs/tech-report-v0.4.0/response-to-reviewers-v0.4.0.md`
- [x] `docs/tech-report-v0.4.0/harness-engineering-source-note.md`
- [x] Superseded pointer on v0.3.0 canonical file
- [ ] Red-team overclaim sweep (OC-1–OC-7) on v0.4.0 body

## Markdown/HTML source and render contract

The Chinese and English Markdown manuscripts are the editable sources. The
self-contained HTML files are rendered editions, not independent prose. Render
both editions from the repository root with Pandoc and the checked-in template,
CSS, and SVG assets:

```bash
pandoc docs/briefloop-architecture-reference-v0.4.0.md \
  --standalone --toc --toc-depth=3 --section-divs \
  --template=docs/assets/briefloop-tech-report-template.html \
  --css=docs/assets/briefloop-tech-report.css --embed-resources \
  --resource-path=docs --metadata=pagetitle:'BriefLoop 架构参考 v0.4.0' \
  --metadata=lang:zh-CN \
  -o docs/briefloop-architecture-reference-v0.4.0.html

pandoc docs/briefloop-architecture-reference-v0.4.0.en.md \
  --standalone --toc --toc-depth=3 --section-divs \
  --template=docs/assets/briefloop-tech-report-template.en.html \
  --css=docs/assets/briefloop-tech-report.css --embed-resources \
  --resource-path=docs --metadata=pagetitle:'BriefLoop Architecture Reference v0.4.0' \
  --metadata=lang:en \
  -o docs/briefloop-architecture-reference-v0.4.0.en.html
```

Pandoc versions may produce different valid section IDs or whitespace, so byte
identity is not the contract. The repository guard checks the meaningful
source/render parity: heading order, fenced code, non-fragment links, valid and
unique fragment targets, embedded CSS, embedded SVG, immutable snapshot
identity, and the documented capability boundary.

```bash
python3 scripts/check_architecture_reference_v04.py
python3 -m pytest -q tests/test_architecture_reference_v04.py
```

---

## Overclaim watch (carry forward from v0.3.0)

| Risk | v0.4.0 mitigation |
|------|-------------------|
| OC-5 v0.9 "already implemented" | Table marks Experimental; deferred items listed separately |
| OC-7 self-improving agent | Unchanged §10.7.6 discipline |
| v1.0 readiness | v0.11.12 has no qualifying first-user evidence; later `v1-pilot-evidence` tracking remains `not_satisfied` |
| Weng synthesis presented as proof | Marked technical synthesis, separated from primary experiments, and paired with explicit no-BriefLoop-evaluation boundary |
