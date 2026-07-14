# 当前架构状态

本页用于区分当前实现状态和 roadmap 目标。按 roadmap 做开发前，先读这里。

公开命名说明：BriefLoop 是 v0.9 兼容期以来的公开项目名。MABW 仍是实现血统和兼容面，
包括 `multi-agent-brief`、`briefloop` shell alias、`/briefloop`、`/mabw`、
Python package/module 路径、artifact 名称、workspace 格式和实验 ID。本页描述当前已实现 runtime 能力，不代表 breaking rename。

## 权限边界和信息来源

- `multi-agent-brief run` / `briefloop run` 是 runtime handoff launcher，不是
  Python 全流程简报生成器。它生成 handoff 和控制面文件，让外部 agent runtime
  按角色执行。
- Runtime identity 由唯一初始化事务显式写入：专用 adapter 固定注入 canonical
  literal，generic CLI 用户必须传 `--runtime`；后续 status、handoff 和 transaction
  只复用 `runtime_manifest.runtime`，不猜测也不改写。历史 `auto` / `manual` / 隐式 `controls`
  manifest 仅供只读诊断，必须显式 reset 后才能继续执行。
- Agent 可以理解、拆分、建议、起草和报告；Python deterministic commands 才能写
  workflow state、artifact registry、event log、freeze metadata、gate reports、
  release/readiness reports 和 delivery/archive manifests。
- 人类触发 delivery。当前控制面没有 force-deliver 路径，也没有 delivery override flag。
  Quality Panel、release readiness、trajectory guidance 等 projection 不能批准交付、
  替代 gate、证明真伪或给出发布授权。
- 有 source、hash、span、page、citation 或 trace 只代表可追溯；不等于语义支持、
  truth proof、hallucination elimination 或 output-quality improvement proof。
- 当 README、roadmap、handoff、skill、private plan 或本页发生冲突时，以当前代码、
  tests、artifact contracts、`docs/support-matrix.md` 和 release guards 为准。
  `private_planning/` 与 roadmap wording 不是已发布能力。

## 已实现的公开基线

- 标准用户路径是 subagent-first。
- `multi-agent-brief run` 生成 运行交接单 artifacts，而不是自己生成完整 brief。
- 运行交接单 会初始化最小 runtime state 和 artifact registry control files。
- Feedback issues 和 bounded repair plans 可以被结构化、校验和记录，但不会自动执行 repair。
- 默认 role topology 允许 Scout 同时完成发现和筛选，同时保持 `candidate_claims.json` 与 `screened_candidates.json` 作为独立 artifacts；strict topology 仍可保留独立 Screener。
- topology-satisfied stages 会记录在 workflow state 和 event log 中；它们不会伪造下游 stage 的独立执行历史。
- Claim Ledger freeze 由 Python 控制面负责：Claim Ledger agents 写不带 claim ID 的 `claim_drafts.json`，然后 `state freeze-claim-ledger` 分配确定性 ID、写 canonical `claim_ledger.json`、记录 freeze metadata，并用冻结账本约束 Claim Ledger stage completion。
- Stage completion transactions 可以在 workflow state 和 event log metadata 中记录该 stage 的 runtime/model provenance；这只是审计 metadata，不是输出质量声明。
- Deterministic material-fact、freshness、target-relevance、
  coverage/omission continuity 和 editor-new-fact gates 可以写入
  stage-scoped 质量门禁 reports，但不会自动找源、改稿、推断完整召回或 repair。
- Packaged public-safe evaluation cases 可以验证 gates、feedback、runtime blocker、
  durable source evidence pack、event-linked release readiness 和 Hermes path
  相关回归，用于开发和 CI。
- Trajectory Regulation 可以在当前 stage 的 retry、repair-cycle 或 repeated-blocker
  budget 耗尽后，确定性地把 `workflow_state.next_allowed_decisions` 收窄为
  `request_human_review` 和 `block_run`。它会把收窄原因写成控制状态和 event-log
  证据，但不会执行 repair、运行 gates、批准 delivery、决定 release readiness，
  也不会替 agent 做工作。
- 可选 deterministic 溯源投影 可以基于已有 control files 写入 workspace-local audit/debug graph。
- Workspace-local audience taste profiles 可以冻结为 per-run snapshots，并通过 handoff 暴露为 runtime context。
- 司乐师 控制台 可以给出 deterministic control recommendations，并记录 enable/defer/reject selections；selection 不会自动执行对应 control。
- Finalize 会把 reader delivery bundle 写入 `output/delivery/`，并把来源附录追加到交付 Markdown/DOCX 末尾；`output/source_appendix.md` 继续作为 audit/control copy 保留。Reader-facing appendix 可以展示安全的 source identity 和 taxonomy labels；`output/source_appendix_trace.md` 可以承载内部 claim/source/span IDs、source paths、source byte hashes 和 metadata completeness warnings 供 audit review。交付产物不得暴露内部 claim IDs、source IDs、evidence text、本地路径或 file URL。
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
  `output/intermediate/claim_support_matrix.json` schema，校验其 Claim
  Ledger / Atomic Claim Graph / Evidence Span Registry 引用，在 matrix 存在时
  要求 high-materiality atom row coverage，并把显式 atom-to-evidence rows
  投影为 status summaries 和 quality-gate findings。这只是 support-record
  control plane，不是 automatic support assessment、semantic proof、release
  eligibility 或 support-sufficiency gate。
- 实验性 Semantic Assessment Report 控制可以校验可选
  `output/intermediate/semantic_assessment_report.json` schema，校验其对 Claim
  Ledger claims、Atomic Claim Graph atoms 和 Evidence Span Registry spans 的
  machine-checkable references，把 rows 投影为 proposal-only Claim-Support
  Matrix delta candidates，并暴露 read-only status counts。这只是 proposal
  surface，不是 accepted support truth、adjudication queue creation、delivery
  gate、release authority 或 semantic proof。
- v0.11 product baseline 已支持三个面向用户的 workspace 入口：
  `briefloop new industry-weekly`、`briefloop new management-monthly` 和
  `briefloop new document-review`。它们分别映射到内部 canonical ReportPack id
  `market_weekly`、`management_monthly` 和 `evidence_extract`，创建保守的
  local-first workspace skeleton，并保留 Claim Ledger、artifact registry、quality
  gates、event log、archive、source appendix、support records、human delivery
  approval 和 frozen-artifact integrity 控制主链。这只是 workspace setup 和
  contract baseline：不会运行 stages、抓取 sources、解析 PDF、批准 delivery、
  证明 truth 或授权 publication。
- 在该基线之外，实验性 ReportSpec / ReportPack / ReportTemplate / PolicyProfile
  控制可以校验
  product-layer `report_spec.yaml`，查看 packaged report pack、section-order
  template contract、PolicyProfile defaults、section conformance diagnostics 和 render-plan
  projection，例如 `solar_industry_periodic`、`manufacturing_default`、
  `solar_manufacturing_default`、`evidence_extract_default`、`finance_default`
  和 `internet_default`，并把已 finalize 的 workspace artifacts 投影为显式
  delivery/audit bundle manifest。Workspaces with `report_spec.yaml` 会在只读
  status 和 generated handoff artifacts 中展示 resolved PolicyProfile 与
  ReportTemplate section order，使 product defaults 和 section contracts 在起草前可追溯。
  ReportTemplate 可以声明 warning-only reader contract，
  检查 required reader blocks、Markdown table slots、executive-summary length
  和 Source Appendix position；这些结果只投影到 status、handoff、
  finalize_report 和 Quality Panel，不成为 gate、delivery approval 或
  release authority。render-plan projection 只读显示 render source artifact、
  section heading mapping、unresolved sections 和 planned delivery targets。
  finalize 期间的 experimental renderer 可以把已存在的 reader Markdown
  sections 按 resolved ReportTemplate 顺序重排，再进入 DOCX generation 和
  reader-final checks；缺失或额外的 top-level sections 只记录 diagnostic/no-op。
  ReportTemplate 还可以声明 reader/audit `citation_profile`（`executive`、
  `analyst` 或 `audit`）。finalize_report 和 bundle manifest 会记录 resolved
  citation profile，使 reader delivery 继续使用读者安全的 source labels，同时
  audit bundle 保留 trace artifacts。这只是 citation surface metadata，不证明
  support、不放松 gates、不移除 audit trace、不批准 delivery，也不决定 release
  readiness。
  `sources materialize-pack` 可以把显式 manual 或 cached-package source records
  materialize 到 `input/sources/`，并可写入 hash-validated
  `source_evidence_pack_manifest.json`，为 recurring reports 提供可归档复现的
  durable source-evidence layer。Source evidence records 会区分 provider/storage
  `source_type`、retrieval/page `retrieval_source_type`、reader-facing
  `source_category` 和 `underlying_evidence_type` metadata；这是 taxonomy
  normalization，不是 trust scoring、source-policy gate 或 semantic support
  judgment。
  `briefloop extract` / `multi-agent-brief extract` 可以在
  `evidence_extract` workspace 中登记显式 extraction scope，并把本地 source
  files 复制到 `input/sources/evidence_extract/`。它还会在
  `output/intermediate/evidence_extract_source_lock.json` 写入确定性的 source
  byte lock，并在 `output/audit/` 保留 audit copy，让后续 status check 能发现
  已登记 source bytes 漂移。它会在
  `output/intermediate/evidence_extract_page_inventory.json` 写入确定性的 page
  inventory seed：UTF-8 文本来源得到一个 logical page ID，PDF/二进制来源只标为
  registered-only 并等待后续 extraction tool。对于 UTF-8 文本来源，它还会在
  `output/intermediate/evidence_span_registry.json` 写入确定性的 text-span
  seed registry，记录 source-text character offsets（`char_start` /
  `char_end`）、page IDs 和 raw-excerpt hashes。它仍然不解析 PDF 或二进制文档、
  不渲染页面做视觉检查、不抽取表格或图、不判断语义支持、不生成 Claim-Support
  Matrix rows、不形成法律或披露结论、不运行 stages、不批准 delivery，也不绕过
  gates。
  Experimental SourceHub Lite setup commands 可以把本地 text evidence files 复制到
  `input/sources/sourcehub/`、登记 RSS feeds，并在 `sources.yaml` 中登记
  `runtime_tool` web-search handoff tasks。这只是 source setup：不会执行 web
  search、crawl web、把 source candidates 或 search summaries 变成 evidence、
  生成 Evidence Span Registry entries、运行 stages、批准 delivery 或绕过 gates。
  Resolved PolicyProfiles 可以通过有限 adapter 收紧现有 deterministic
  quality-gate strictness 和 reader-final forbidden-phrase checks，但 gates 不会从
  natural-language industry strings 静默推断 policy。
  Internal release-mode approval commands 可以初始化 `human_approval_ledger.json`、
  追加 human approval decisions，并写入带 event-log linkage 的
  `release_readiness_report.json`。这些 checks 区分 internal readiness 和
  authorization：它们不会对外发布、替代 legal/compliance/IR owners，或绕过已有
  gates 和 human delivery approval。
  Quality Panel projection 可以把现有 control integrity、source
  evidence、gate、claim/support 和 delivery hygiene surfaces 汇总为
  `output/intermediate/quality_panel.json`，并可生成 SHA-bound
  `output/intermediate/quality_summary.md` 和 no-JavaScript
  `output/intermediate/quality_panel.html` audit attachment。`state
  finalize-complete` CLI 只有在权威 transaction、archive 和 `run_archived` event
  全部完成后，才调用与 `quality summarize` 相同的 deterministic closeout writer，
  再由 `state check` 刷新 Artifact Registry。交互式人工 CLI 会尝试用默认浏览器
  打开静态 HTML；JSON 或非交互调用不会打开浏览器，展示失败也不改变 finalize 或
  closeout truth。`quality summarize` 继续作为手动修复入口；直接调用 Python
  finalize transaction 不承诺这个 CLI-only hook。report bundle projection 可把有效的
  QP artifacts 放进 audit bundle，但不会放入 reader-facing delivery bundle。三个可修复的
  QP projection artifacts 永远不属于 immutable finalized-run archive，即使 finalize 前
  已存在较早投影；已生成 archive 也不会因事务后 closeout 被改写。这些 projection 不运行 gates、不创建
  quality score、不替代 gate reports、不决定 release eligibility、不批准
  delivery、不证明 semantic truth，也不执行 repair。
  Experimental Guidance Manifestation projection 可以读取可选
  `output/intermediate/guidance_manifestation_report.json` 中对已 materialized
  approved guidance entries 的 human/imported diagnostic labels，并在 status 和
  Quality Panel 中暴露 `explicitly_reflected`、`partially_reflected`、
  `contradicted` 和 `not_observable` counts。Python 只校验和计数这些 labels；
  它不判断 manifestation、不修改 Improvement Memory、不批准 guidance、不创建
  quality score、不运行 gates、不批准 delivery，也不决定 release readiness。
  Experimental Materiality Selection projection 可以读取有效
  `screened_candidates.json`、resolved PolicyProfile materiality terms 和
  workspace focus terms，提示在 capacity/scope screening 后被 excluded 或
  deprioritized、但匹配显式 materiality/focus terms 的 candidates。它只是
  deterministic keyword diagnostics：不推断语义重要性、不修改 screening output、
  不 resurrect candidates、不改变 Claim Ledger、不运行 gates、不批准 delivery，
  也不决定 release readiness。
  Experimental Support-Calibrated Wording projection 可以读取已有 reader
  Markdown、Claim Ledger metadata、source taxonomy 和有效 Claim-Support Matrix
  policy signals，输出 warning-only 的 `support_wording` diagnostics，用于提示
  weak / downgrade-required / inferential / unsupported / media-report support
  与强措辞或缺少归因/不确定性框架之间的错配。它只是确定性 lexical
  projection，不判断 claim 真伪、不生成或接受 support rows、不运行 gates、
  不阻断 delivery、不批准 release，也不创建 quality score。
  这些契约只在现有 Claim Ledger、artifact registry、gates、event log、
  archive、source appendix、support records、frozen-artifact integrity 和 human
  delivery approval 主链之上描述 report type metadata。这些 product-layer
  surfaces 不运行 stages、不创建第二套 gate engine、不把 section/render-plan
  diagnostics 变成 gates、不把 source plans/search summaries 变成 evidence、不创建
  semantic support assessor、不判断行业合规、不验证 internet rumors、不绕过 gates、
  不批准 delivery、不提供税务或投资建议，也不授权发布。
- Python 命令负责 setup、source tooling、validation、audit support 和 rendering。
- Hermes、Claude Code、Codex、OpenCode 和 operator fallback 都是 agent runtime surfaces。
  operator runtime 是适用于没有专用 runtime adapter 宿主的操作者模式；
  legacy `manual` 只保留为 CLI 兼容别名，不是 Python brief-generation path。
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
- 延后处理的 semantic-governance 结构，例如 semantic support scoring、human
  adjudication、release eligibility 和 support-sufficiency gates；这些不是
  v0.9 support core 后的默认下一阶段实现主线

## Experimental 或有限能力

标记为 experimental、interface-only 或 CLI-only 的能力，不应被当成稳定用户承诺。使用前先查 support matrix 和 CLI 输出。

## Contributor 规则

roadmap 方向不等于已实现代码。实现 roadmap item 前，先确认当前代码路径、对应 validator 或 test，以及该能力属于 public、experimental 还是 internal planning。
