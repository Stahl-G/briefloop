# MABW Architecture Reference v0.3.0 — Revision Roadmap

**Revision Round**: 1 (v0.2.0 → v0.3.0)
**Date**: 2026-06-19
**Previous Version**: v0.2.0 (code snapshot v0.8.3)
**Target Version**: v0.3.0 (code snapshot v0.9.x)
**Revision Type**: Major — new chapter, updated abstract, expanded related work, updated implementation baseline

---

## Paper Information

| Field | Value |
|-------|-------|
| Paper Title | MABW：合约治理多智能体企业简报工作流 — 架构参考 v0.3.0 |
| Revision Round | 1 |
| Date | 2026-06-19 |
| Previous Decision | N/A (self-directed revision) |
| Target | Internal tech report / architecture reference |
| Original Word Count | ~688 lines (~25,000 CJK characters) |
| Revised Word Count | TBD |

---

## Revision Tracking Table

| # | Issue Description | Reviewer | Type | Section | Priority | Status |
|---|-------------------|----------|------|---------|----------|--------|
| 1 | 新增章节：Industrial Evidence（产业实践证据） | Self | Major | §10.7 (新增) | P1 | PENDING |
| 2 | 重写摘要：强调 v0.9 从 traceability → support sufficiency | Self | Major | 摘要 | P1 | PENDING |
| 3 | A1: Enterprise AI category — 入口脚注 | Self | Minor | §10.7 开头 | P2 | PENDING |
| 4 | A2: Cowork product guide — chat → deliverable agent | Self | Major | §1.3 / §10.7 | P1 | PENDING |
| 5 | A3: Cowork best practices — 定义 ICP（task shape） | Self | Major | §1.2 / §10.7 | P1 | PENDING |
| 6 | A4: Self-service analytics — verification problem | Self | Major | §5 / §10.7 / §11.3 | P1 | PENDING |
| 7 | A5: Kepler — deterministic infrastructure + pipeline boundary | Self | Major | §10.7 / §3 | P1 | PENDING |
| 8 | A6: Claude Code large codebases — harness > prompt | Self | Major | §1.4 / §10.7 | P1 | PENDING |
| 9 | A7: Zero Trust — enterprise security surfaces | Self | Major | §10.7 / §11 | P1 | PENDING |
| 10 | A8: Enterprise-managed auth — connector governance | Self | Minor | §10.7 / §11.4 | P2 | PENDING |
| 11 | A9: Multi-agent when/how — 降温论证 | Self | Major | §10.7 / §1.0 | P1 | PENDING |
| 12 | A10: Coordination patterns — generator-verifier 界限 | Self | Major | §10.7 / §5 | P1 | PENDING |
| 13 | B1: Managed Agents — production harness primitives | Self | Minor | §10.7 | P3 | PENDING |
| 14 | B2: Agentic surfaces — prototype vs production gap | Self | Minor | §10.7 | P3 | PENDING |
| 15 | B3: Agent Skills — procedural knowledge packaging | Self | Minor | §10.7 | P3 | PENDING |
| 16 | B4: Citations API — passage-level traceability signal | Self | Minor | §10.7 / §5 | P3 | PENDING |
| 17 | B5: Claude Enterprise self-serve — admin controls | Self | Minor | §10.7 | P3 | PENDING |
| 18 | B6: MCP production systems — connector design | Self | Minor | §10.7 | P3 | PENDING |
| 19 | B7: Cowork plugins — enterprise workflow packaging | Self | Minor | §10.7 | P3 | PENDING |
| 20 | 新增论证链 §1: chat → deliverable agent | Self | Major | §10.7.1 | P1 | PENDING |
| 21 | 新增论证链 §2: source traceability → support sufficiency | Self | Major | §10.7.2 / §5 | P1 | PENDING |
| 22 | 新增论证链 §3: prompt → harness | Self | Major | §10.7.3 | P1 | PENDING |
| 23 | 新增论证链 §4: LLM judge → adjudication-ready control record | Self | Major | §10.7.4 / §5 | P1 | PENDING |
| 24 | 新增论证链 §5: workflow automation → enterprise governance | Self | Major | §10.7.5 | P1 | PENDING |
| 25 | A+1: OpenAI Tax AI — 生产闭环自我改进 | Self | Major | §10.7.6 / §5 / §11.3 | P1 | PENDING |
| 26 | 新增 Finding Candidate System 设计 | Self | Major | v09-design-rationale §9 | P1 | PENDING |
| 27 | Red-team: 标记所有过度表述 | Self | Major | 全文 | P1 | PENDING |

---

## Commitment Ledger

```yaml
# concern 1: 新增 Industrial Evidence 章节
- concern_id: SELF-1
  commitment_extracted:
    - commitment_text: "新增 §10.7 Industrial Evidence 章节，包含 5 个子节"
      commitment_type: add_section
      required_evidence_type: new_section
    - commitment_text: "创建 citation matrix 文档 docs/research/anthropic-enterprise-ai-citation-matrix.md"
      commitment_type: add_section
      required_evidence_type: new_section
    - commitment_text: "创建 industrial-related-work.md 草稿"
      commitment_type: add_section
      required_evidence_type: new_section
    - commitment_text: "创建 v09-design-rationale.md 草稿"
      commitment_type: add_section
      required_evidence_type: new_section

# concern 2: 重写摘要
- concern_id: SELF-2
  commitment_extracted:
    - commitment_text: "重写摘要，强调 v0.9 从 traceability → support sufficiency 过渡"
      commitment_type: restructure
      required_evidence_type: prose_edit

# concern 3-19: A 类 + B 类引用
- concern_id: SELF-3
  commitment_extracted:
    - commitment_text: "A1 Enterprise AI category 入口脚注"
      commitment_type: add_citation
      required_evidence_type: prose_edit

- concern_id: SELF-4
  commitment_extracted:
    - commitment_text: "A2 Cowork product guide 引用：chat → deliverable agent"
      commitment_type: add_citation
      required_evidence_type: discussion_paragraph

- concern_id: SELF-5
  commitment_extracted:
    - commitment_text: "A3 Cowork best practices 引用：定义 ICP task shape"
      commitment_type: add_citation
      required_evidence_type: discussion_paragraph

- concern_id: SELF-6
  commitment_extracted:
    - commitment_text: "A4 Self-service analytics 引用：verification problem + semantic layer"
      commitment_type: add_citation
      required_evidence_type: discussion_paragraph

- concern_id: SELF-7
  commitment_extracted:
    - commitment_text: "A5 Kepler 引用：deterministic infrastructure + pipeline boundary"
      commitment_type: add_citation
      required_evidence_type: discussion_paragraph

- concern_id: SELF-8
  commitment_extracted:
    - commitment_text: "A6 Claude Code large codebases 引用：harness > prompt"
      commitment_type: add_citation
      required_evidence_type: discussion_paragraph

- concern_id: SELF-9
  commitment_extracted:
    - commitment_text: "A7 Zero Trust 引用：enterprise security surfaces"
      commitment_type: add_citation
      required_evidence_type: discussion_paragraph

- concern_id: SELF-10
  commitment_extracted:
    - commitment_text: "A8 Enterprise-managed auth 引用：connector governance"
      commitment_type: add_citation
      required_evidence_type: prose_edit

- concern_id: SELF-11
  commitment_extracted:
    - commitment_text: "A9 Multi-agent when/how 引用：降温论证"
      commitment_type: add_citation
      required_evidence_type: discussion_paragraph

- concern_id: SELF-12
  commitment_extracted:
    - commitment_text: "A10 Coordination patterns 引用：generator-verifier 界限"
      commitment_type: add_citation
      required_evidence_type: discussion_paragraph

# concern 13-19: B 类引用（均为 add_citation + prose_edit）
- concern_id: SELF-13
  commitment_extracted:
    - commitment_text: "B1 Managed Agents 引用"
      commitment_type: add_citation
      required_evidence_type: prose_edit

- concern_id: SELF-14
  commitment_extracted:
    - commitment_text: "B2 Agentic surfaces 引用"
      commitment_type: add_citation
      required_evidence_type: prose_edit

- concern_id: SELF-15
  commitment_extracted:
    - commitment_text: "B3 Agent Skills 引用"
      commitment_type: add_citation
      required_evidence_type: prose_edit

- concern_id: SELF-16
  commitment_extracted:
    - commitment_text: "B4 Citations API 引用"
      commitment_type: add_citation
      required_evidence_type: prose_edit

- concern_id: SELF-17
  commitment_extracted:
    - commitment_text: "B5 Claude Enterprise self-serve 引用"
      commitment_type: add_citation
      required_evidence_type: prose_edit

- concern_id: SELF-18
  commitment_extracted:
    - commitment_text: "B6 MCP production systems 引用"
      commitment_type: add_citation
      required_evidence_type: prose_edit

- concern_id: SELF-19
  commitment_extracted:
    - commitment_text: "B7 Cowork plugins 引用"
      commitment_type: add_citation
      required_evidence_type: prose_edit

# concern 20-24: 五条论证链
- concern_id: SELF-20
  commitment_extracted:
    - commitment_text: "论证链 1: chat → deliverable agent（Cowork + best practices）"
      commitment_type: add_section
      required_evidence_type: new_section

- concern_id: SELF-21
  commitment_extracted:
    - commitment_text: "论证链 2: source traceability → support sufficiency（analytics + Kepler）"
      commitment_type: add_section
      required_evidence_type: new_section

- concern_id: SELF-22
  commitment_extracted:
    - commitment_text: "论证链 3: prompt → harness（Claude Code + Managed Agents + Skills）"
      commitment_type: add_section
      required_evidence_type: new_section

- concern_id: SELF-23
  commitment_extracted:
    - commitment_text: "论证链 4: LLM judge → adjudication-ready control record（coordination patterns）"
      commitment_type: add_section
      required_evidence_type: new_section

- concern_id: SELF-24
  commitment_extracted:
    - commitment_text: "论证链 5: workflow automation → enterprise governance（Zero Trust + auth）"
      commitment_type: add_section
      required_evidence_type: new_section

# concern 25: Red-team pass
- concern_id: SELF-25
  commitment_extracted:
    - commitment_text: "Red-team pass：标记所有过度表述（MABW proves truth / eliminates hallucination / Anthropic proves MABW / Citations = support sufficiency / multi-agent guarantees quality / roadmap items already implemented）"
      commitment_type: add_analysis
      required_evidence_type: prose_edit
```

---

## Section Mapping

| Section | Changes Required | Concerns |
|---------|-----------------|----------|
| **摘要** | 重写：强调 v0.9 support sufficiency 过渡 | SELF-2 |
| **§1.0 架构宪章** | 补充 multi-agent 降温论证 | SELF-11 |
| **§1.2 业务工作流缺乏什么** | 补充 Cowork best practices 作为 ICP 定义 | SELF-5 |
| **§1.3 MABW 核心论点** | 补充 Cowork product guide 作为 deliverable agent 证据 | SELF-4 |
| **§1.4 运行时接口收敛** | 补充 Claude Code large codebases harness 论证 | SELF-8 |
| **§3 架构骨干** | 补充 Kepler 作为 deterministic infrastructure 证据 | SELF-7 |
| **§5 证据与声明治理** | 补充 analytics verification problem + coordination patterns | SELF-6, SELF-10, SELF-12, SELF-16 |
| **§10 相关工作** | 新增 §10.7 Industrial Evidence 章节（含 §10.7.6 Tax AI） | SELF-1, SELF-3~19, SELF-20~24, SELF-25~26 |
| **§11 局限性与未来工作** | 补充 Zero Trust + connector governance | SELF-9, SELF-10 |
| **附录** | 无变更 | — |
| **全文** | Red-team pass 检查过度表述 | SELF-25 |

---

## Priority Summary

| Priority | Count | Description |
|----------|-------|-------------|
| P1 (must_fix) | 19 | 核心论证链、A+ 类引用、A 类引用、摘要重写、新章节、Finding Candidate、red-team |
| P2 (should_fix) | 3 | 入口脚注、connector governance |
| P3 (consider) | 5 | B 类补充引用 |

---

## Deliverables Checklist

- [x] `docs/research/anthropic-enterprise-ai-citation-matrix.md` — 引用矩阵（18 篇）
- [x] `docs/tech-report-v0.3.0/industrial-related-work.md` — 产业实践证据章节草稿（含 §10.7.6 Tax AI）
- [x] `docs/tech-report-v0.3.0/v09-design-rationale.md` — v0.9 设计原理连接文档（含 Finding Candidate）
- [x] `docs/tech-report-v0.3.0/abstract-draft-v0.3.0.md` — v0.3.0 摘要替换草稿
- [ ] Red-team pass 报告（过度表述标记）

---

## Overclaim Risk Register

| Risk | Pattern | Location | Mitigation |
|------|---------|----------|------------|
| OC-1 | "Anthropic proves MABW" | 相关工作 | 改为 "industry evidence supports the task shape" |
| OC-2 | "MABW proves truth" | 摘要/结论 | 改为 "MABW operationalizes support sufficiency" |
| OC-3 | "Citations = support sufficiency" | §5 | 明确 Citations 是 passage-level, v0.9 走到 atom-level |
| OC-4 | "Multi-agent guarantees quality" | §10.7 | 引用 A9 降温 + MABW 核心是 contract 不是 agent 数量 |
| OC-5 | "v0.9 roadmap items already implemented" | 全文 | 严格区分 v0.8.3 implemented vs v0.9 planned |
| OC-6 | "Anthropic internal metrics = MABW results" | §10.7 | 标注 "reported industrial practice, not MABW measured result" |
| OC-7 | "MABW is self-improving" | §10.7.6 | 改为 "MABW operationalizes failure-to-finding-to-repair as structured workflow" |

---

## Response Letter Skeleton

> 本次修订（v0.2.0 → v0.3.0）是一次自驱动的重大修订，主要变更包括：
>
> 1. **新增 §10.7 Industrial Evidence 章节**：引入 1 篇 A+ 类（OpenAI Tax AI）、10 篇 A 类、7 篇 B 类官方文献，作为企业 AI 从 chat 走向 deliverable agent、harness、governance、verification layer、production self-improvement 的产业实践证据。
>
> 2. **六条论证链**：(1) chat → deliverable agent, (2) traceability → support sufficiency, (3) prompt → harness, (4) LLM judge → adjudication-ready control record, (5) workflow automation → enterprise governance, (6) production trace as substrate for self-improvement。
>
> 3. **摘要重写**：明确 v0.9 的定位是从 source-level traceability 推进到 evidence-span-level support sufficiency，而非 semantic truth proof。
>
> 4. **Finding Candidate System 设计**：以 OpenAI Tax AI 作为独立收敛的生产级参照，将 MABW 的改进闭环定义为 failure → structured finding → eval target → scoped repair → regression → human review 的工程流程。
>
> 5. **Red-team pass**：标记并修正所有过度表述，守住 "MABW operationalizes support sufficiency, not proves truth" 的口径。
>
> 所有新增引用均标注为 "industry evidence / practitioner evidence / official case study"，不作为学术 proof 使用。

---

## Summary Statistics

| Metric | Count |
|--------|-------|
| Total items | 27 |
| P1 (must_fix) | 19 |
| P2 (should_fix) | 3 |
| P3 (consider) | 5 |
| New sections | 7 (§10.7 + 5 论证链子节 + §10.7.6 Tax AI) |
| New citations (A+-class) | 1 |
| New citations (A-class) | 10 |
| New citations (B-class) | 7 |
| Overclaim risks | 6 |

---

## Revision Completeness Checklist

- [ ] A+1 OpenAI Tax AI 在 §10.7.6 有完整段落
- [ ] 每条 A 类引用在 §10.7 有对应段落
- [ ] 每条 B 类引用在 §10.7 有对应段落（较短）
- [ ] 六条论证链各自有独立子节
- [ ] Finding Candidate System 在 v09-design-rationale §9 有完整设计
- [ ] 摘要已重写，包含 "support sufficiency" 和 "not proves truth"
- [ ] v0.8.3 implemented 和 v0.9 planned 严格区分
- [ ] 所有 Anthropic 内部指标标注为 "reported industrial practice"
- [ ] Citations API 明确为 passage-level，不等于 support sufficiency
- [ ] Multi-agent 论证包含 A9 的降温警告
- [ ] Kepler 标注为 official case study，非 academic benchmark
- [ ] OpenAI Tax AI 标注为 engineering case study，不写 "MABW is self-improving"
- [ ] Red-team pass 完成，无过度表述残留
