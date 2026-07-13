# Response to Reviewers — Architecture Reference v0.3.0 → v0.4.0

**Revision mode**: `academic-paper` / `revision` (fidelity spectrum, high oversight)
**Base manuscript**: `docs/briefloop-architecture-reference-v0.3.0.md` (code snapshot v0.8.3)
**Revised manuscript**: `docs/briefloop-architecture-reference-v0.4.0.md` (code snapshot v0.11.12)
**Date**: 2026-07-10

---

## Reviewer roles (synthetic — repository sync review)

Because no external journal reviews were supplied, this round treats three
internal review lenses as the comment source:

| Reviewer | Lens |
|----------|------|
| **R-Impl** | Implementation drift (report vs `VERSION` 0.11.12) |
| **R-Claim** | Overclaim register OC-5 / OC-7 from v0.3.0 roadmap |
| **R-Product** | v0.11 product baseline and v1.0 evidence gate |

---

## Point-by-point response

### Major

| ID | Comment | Response | Location in v0.4.0 |
|----|---------|----------|-------------------|
| **M1** | §8 still describes v0.8.3; repo is v0.11.12 approaching v1.0. | **Accepted.** Replaced §8.1–8.3 with a v0.8.3→immutable-v0.11.12 evolution table, snapshot-scoped Supported/Experimental/Deferred lists, and an explicit post-snapshot v1.0 evidence pointer (`not_satisfied`). | §8 |
| **M2** | v0.9 surfaces listed as "Planned" but many exist as Experimental. | **Accepted.** Added §3.6 and §11.3 implementation-status table; created `tech-report-v0.4.0/v09-implementation-status.md`. Wording distinguishes *Experimental* from *Supported* and repeats non-blocking boundaries. | §3.6, §11.3, appendix |
| **M3** | Missing v0.11 Product OS (briefloop new, Quality Panel, bundles). | **Accepted.** Documented Product OS spine, three product entries, Quality Panel/bundle projection, transactional finalize promotion. | §3.6, §8.2 |
| **M4** | The July 4 harness-engineering synthesis postdates BriefLoop's core spine but gives the report a stronger external research frame. Integrate it without rewriting project history or treating a technical essay as experimental proof. | **Accepted.** Added a tag-grounded chronology, classified Weng (2026) as research synthesis, retained primary-paper attribution for mechanisms, and specified a Proposed bounded improvement protocol with external evaluator/permission and held-out regression. | §1.4, §7.5, §10.1, §11, App G |
| **M5** | The Chinese manuscript reads like a mixed-language incremental patch and still treats the retired name as a parallel brand. | **Accepted.** Rewrote the manuscript in Chinese, normalized control-plane terminology, and limited the retired name to literal archived or compatibility identifiers. | 全文、命名政策 |

### Minor

| ID | Comment | Response | Location in v0.4.0 |
|----|---------|----------|-------------------|
| **m1** | Abstract still centers v0.8.3 traceability only. | **Revised** abstract to lead with v0.11.12 baseline + experimental v0.9 stack + v1.0 gate. | 摘要 |
| **m2** | Eval case count "11+" and test count "1500+" are stale. | **Updated** to 25 eval-cases and 2767+ tests (pytest collect, 2026-07-09). | §8.2, Appendix E |
| **m3** | Appendix D omits Delivery Editor and role topology. | **Updated** pipeline and `default`/`strict`/`human_assisted` topology. | Appendix D |
| **m4** | §9 lacks BriefLoop-090 / v0.11.3 reference evidence. | **Added** §9.5 with supported vs unsupported claims. | §9.5 |
| **m5** | Appendix I rename timeline still says PR0 pending. | **Updated** timeline: PR0 complete; v1.0 in progress. | Appendix I |
| **m6** | Trajectory regulation still described as future work in §10.1. | **Corrected** to v0.11 Trajectory Regulation (decision narrowing). | §10.1 |
| **m7** | §7.3 guidance manifestation still "Planned v0.8.5". | **Updated** to Experimental projection + eval-case reference. | §7.3 |
| **m8** | The report uses "self-improvement" in several places without one explicit statement of what v0.11.12 does not implement. | **Updated.** §11.1 now states that automatic weakness clustering, harness-proposal acceptance, and held-out no-regression evidence are not shipped. | §11.1 |
| **m9** | The report lacks a publication-quality architecture overview and a browser-native reading edition. | **Updated.** Added a three-lane architecture figure that separates agent work, governed artifacts, and deterministic authority; added a self-contained HTML reading edition. | §3, HTML edition |
| **m10** | External readers need a first-class English edition rather than a browser-translated Chinese page. | **Updated.** Added a full English manuscript, an English architecture figure, and a self-contained English HTML edition. | English edition |

---

## Declined / deferred (scope discipline)

| Item | Decision | Reason |
|------|----------|--------|
| Full rewrite of §10.7 industrial evidence | **Deferred** | Still accurate as practitioner evidence; v0.4.0 focuses on implementation sync. |
| Implement Issue Candidate schema | **Deferred** | Appendix H now records only the not-shipped product boundary; no public field/schema draft is defined. |
| Claim v1.0 readiness | **Declined** | The immutable v0.11.12 snapshot contains no qualifying first-user evidence record. The later `docs/v1-pilot-evidence.md` tracking file remains `not_satisfied`; the report states approaching v1.0, not released v1.0. |

---

## Revision artifacts

| Artifact | Path |
|----------|------|
| Revised draft | `docs/briefloop-architecture-reference-v0.4.0.md` |
| HTML reading edition | `docs/briefloop-architecture-reference-v0.4.0.html` |
| Architecture figure | `docs/assets/briefloop-architecture-v0.4.0.svg` |
| English manuscript | `docs/briefloop-architecture-reference-v0.4.0.en.md` |
| English HTML edition | `docs/briefloop-architecture-reference-v0.4.0.en.html` |
| English architecture figure | `docs/assets/briefloop-architecture-v0.4.0.en.svg` |
| Implementation baseline note | `docs/tech-report-v0.4.0/implementation-baseline-v0.11.md` |
| v0.9 status matrix | `docs/tech-report-v0.4.0/v09-implementation-status.md` |
| Harness-engineering source note | `docs/tech-report-v0.4.0/harness-engineering-source-note.md` |
| Revision roadmap | `docs/briefloop-architecture-reference-v0.4.0-revision-roadmap.md` |
| Historical base (superseded) | `docs/briefloop-architecture-reference-v0.3.0.md` |

---

## Author statement (public claims discipline)

This revision synchronizes the architecture reference narrative to the v0.11.12
codebase. It does **not** constitute output-quality proof, semantic truth proof,
management-ready delivery certification, or v1.0 release authorization.
