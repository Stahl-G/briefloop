# 当前架构状态

本页用于区分当前实现状态和 roadmap 目标。按 roadmap 做开发前，先读这里。

公开命名说明：BriefLoop 是 v0.9 兼容期的公开项目名。MABW 仍是实现血统和兼容面，
包括 `multi-agent-brief`、`/mabw`、Python package/module 路径、artifact 名称、
workspace 格式和实验 ID。本页描述当前已实现 runtime 能力，不代表 breaking rename。

## 已实现的公开基线

- 标准用户路径是 subagent-first。
- `multi-agent-brief run` 生成 运行交接单 artifacts，而不是自己生成完整 brief。
- 运行交接单 会初始化最小 runtime state 和 artifact registry control files。
- Feedback issues 和 bounded repair plans 可以被结构化、校验和记录，但不会自动执行 repair。
- 默认 role topology 允许 Scout 同时完成发现和筛选，同时保持 `candidate_claims.json` 与 `screened_candidates.json` 作为独立 artifacts；strict topology 仍可保留独立 Screener。
- topology-satisfied stages 会记录在 workflow state 和 event log 中；它们不会伪造下游 stage 的独立执行历史。
- Claim Ledger freeze 由 Python 控制面负责：Claim Ledger agents 写不带 claim ID 的 `claim_drafts.json`，然后 `state freeze-claim-ledger` 分配确定性 ID、写 canonical `claim_ledger.json`、记录 freeze metadata，并用冻结账本约束 Claim Ledger stage completion。
- Stage completion transactions 可以在 workflow state 和 event log metadata 中记录该 stage 的 runtime/model provenance；这只是审计 metadata，不是输出质量声明。
- Deterministic material-fact、freshness、target-relevance 和 editor-new-fact gates 可以写入 stage-scoped 质量门禁 reports，但不会自动找源、改稿或 repair。
- Packaged public-safe evaluation cases 可以验证 gates、feedback、runtime blocker 和 Hermes path 相关回归，用于开发和 CI。
- 可选 deterministic 溯源投影 可以基于已有 control files 写入 workspace-local audit/debug graph。
- Workspace-local `audience_profile.md` 可以记录 reader taste；`run`、`start` 和 `handoff` 会创建或复用 frozen per-run `output/intermediate/audience_profile_snapshot.md`，并通过 handoff 暴露为 runtime context。
- 司乐师 控制台 可以给出 deterministic control recommendations，并记录 enable/defer/reject selections；selection 不会自动执行对应 control。
- Finalize 会把 reader delivery bundle 写入 `output/delivery/`，并把来源附录追加到交付 Markdown/DOCX 末尾；`output/source_appendix.md` 继续作为 audit/control copy 保留。交付产物不得暴露内部 claim IDs、source IDs、evidence text、本地路径或 file URL。
- Runtime asset availability 已显式区分：package install 包含 契约 configs 和 public-safe eval fixtures；`.agents/`、`.claude/`、`.opencode/`、`.codex/` 以及 Hermes plugin 文件属于 source-clone-only，除非通过 `multi-agent-brief runtime install` 复制到 workspace。
- Improvement Ledger lifecycle 可以把人工撰写、人工批准的读者偏好保存在 `improvement/ledger.jsonl`，将 approved 且可物化的 entries 投影到 `improvement/memory.md`，在每次 run 冻结为 `output/intermediate/improvement_memory_snapshot.md`，并且只通过 handoff 暴露 frozen snapshot。
- Packaged public-safe evaluation cases 已覆盖 Improvement Memory 控制行为：未批准 entry 不物化，已批准 guidance 会冻结，reverted entry 会从下一次 snapshot 中移除。
- 实验性 Atomic Claim Graph 控制可以校验可选
  `output/intermediate/atomic_claim_graph.json`，检查 whole-ledger coverage 和
  deterministic Claim Ledger type consistency，暴露 Analyst/Editor
  no-new-atom contract boundary，并投影 reader-facing atom residue。这只是结构可见性，不是
  evidence-span support sufficiency。
- 实验性 Evidence Span Registry 控制可以校验可选
  `output/intermediate/evidence_span_registry.json`，把声明的 spans 绑定到
  durable `input/sources/` bytes，归档 span/source hashes，并投影 reader-safe
  Source Appendix span summary 和独立的 `output/source_appendix_trace.md` audit
  copy。这只是 span-level traceability 和 archive reproducibility，不是 semantic
  support assessment 或 support-sufficiency gate。
- 实验性 Claim-Support Matrix 控制可以校验可选
  `output/intermediate/claim_support_matrix.json` schema，并把显式
  atom-to-evidence support rows 投影成 deterministic atom-level policy signals。
  这只是 support-record structure，不是 automatic support assessment、semantic
  proof 或 release eligibility。
- Python 命令负责 setup、source tooling、validation、audit support 和 rendering。
- Hermes、Claude Code、Codex、OpenCode 和 manual fallback 都是 agent runtime surfaces。
- Input governance 可以先用 MinerU 把受支持的非文本输入抽取为 Markdown，再区分 evidence、feedback、instructions 和 background context。
- 旧 Python-pipeline 叙事不再是标准 workflow。

## Roadmap 目标

roadmap 中提到的概念不一定已经实现。除非代码、测试和 support matrix 已确认，否则都按目标处理：

- 司乐师 契约
- semantic evidence support verification
- quality evaluation and feedback loops
- policy packs
- public-safe reference workflows
- smart routing or automatic taste learning
- FrictionStore、retrieval memory、runtime-specific guidance filtering 和 output-quality validation
- v0.9 support-sufficiency 后续结构，例如 semantic support scoring、human
  adjudication、release eligibility 和 support-sufficiency gates

## Experimental 或有限能力

标记为 experimental、interface-only 或 CLI-only 的能力，不应被当成稳定用户承诺。使用前先查 support matrix 和 CLI 输出。

## Contributor 规则

roadmap 方向不等于已实现代码。实现 roadmap item 前，先确认当前代码路径、对应 validator 或 test，以及该能力属于 public、experimental 还是 internal planning。
