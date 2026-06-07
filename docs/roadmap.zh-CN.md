# 路线图

本路线图反映 v0.5.7 后的新基线：subagent-first runtime handoff + Hermes primary path + input governance。完整 agent 参考见 [v1 前置收敛路线图](agents/reference/v1-pre-mas-refactor-roadmap.zh-CN.md)。

## 已完成（v0.1 — v0.5.7）

v0.5.7 已将项目从旧 Python pipeline 推到新基线：

- **Subagent-first runtime handoff**：`multi-agent-brief run` 不再是 Python 生成 brief，而是 runtime handoff launcher。外部子智能体 workflow：scout → screener → claim-ledger → analyst → editor → auditor → finalize。
- **Hermes primary path**：Hermes 适配层提供 `delegate_task` 原生子代理管线、cron 调度、daily source cache 和 cached_package 对接。
- **Thin CLI router**：`main.py` 瘦化为路由层，命令逻辑分布在 13 个 `cli/*_commands.py` 模块。
- **平台适配器**：Claude Code、OpenCode、Codex 三平台子智能体定义由 `configs/agent_roles.yaml` 统一生成。
- **Input governance**：`inputs classify` CLI 命令 + Scout 证据目录契约 + ManualProvider 硬门禁，阻止反馈/指令/背景文件污染 Claim Ledger。
- **Quality gates**：deterministic audit、editorial governance、final quality、limitation hygiene 四层质量门。
- **Analysis modules**：market competitor 和 policy regulatory 两个性质不同的可插拔模块。

## 总原则

v1.0 之前不应继续堆搜索后端、专题模块或交付渠道。当前优先级：

```text
Release & Runtime Contract Cleanup
→ Runtime Artifact Contract
→ Quality Mainline（分析块正式化 + 质量门）
→ Golden Runs & Evaluation
→ Packaging & Distribution
→ v1.0 Stable Baseline
→ v2.0 MAS Runtime（推迟）
```

---

## v0.5.8：Release And Runtime Contract Cleanup

**目标**：让 v0.5.7 的新架构不自相矛盾。

### 必须做

- **修复 Issue [#49](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/49)**：明确 clone/source install 与 CLI-only install 的边界，或将 Hermes plugin / agent assets 打进 package。
- **更新 Homebrew formula**：当前 `Formula/multi-agent-brief.rb` 仍指向旧版本。
- **打 v0.5.7 tag** 或调整 release consistency 规则，否则 CI 继续卡。
- **重写 `docs/roadmap.zh-CN.md` 和 `docs/architecture.zh-CN.md`**：删除主路径里的 `prepare` 和旧 Python pipeline 叙事。
- **建立 Support Matrix**：明确 `Supported / Experimental / Interface Only / CLI-only / Deprecated`，覆盖 Hermes、OpenCLI、local_signal、delivery、PDF、Homebrew/curl 等。

### 验收标准

- CI 全绿，不再因为 tag 漂移报错。
- README、AGENTS.md、CLAUDE.md 与当前实际入口一致。
- Support Matrix 文档存在，每个能力有明确状态标签。

---

## v0.5.9：Runtime Artifact Contract

**目标**：让 subagent workflow 可验收，而不是只靠 prompt 顺序。

### 必须做

- **`run_manifest.json` 复用**：由 handoff/runtime 阶段记录每个中间 artifact 的存在、hash、producer、status。Scout/screener/claim-ledger/analyst/editor/auditor 完成后更新。
- **新增 artifact validators**：`validate-candidates`、`validate-screened`、`validate-ledger`、`validate-handoff`——每个子智能体输出后自动校验。
- **`inputs classify` 结果进入 `agent_handoff.json`**：Scout 必须按 evidence list 执行，不可自由扫描全 `input/`。
- **Runtime parity tests**：Hermes / Claude Code / OpenCode / Codex 的 artifact contract 做一致性测试。
- **RelevanceGate**：输出 `output/intermediate/relevance_report.json`，放在 claim-ledger 之后、analyst 之前。决定哪些 claim 能进正文、摘要、附录或丢弃。

### 验收标准

- 任意 runtime 执行后，`run_manifest.json` 完整记录 artifact 链。
- CI 可以跑 `validate-handoff`，不依赖 LLM 判断。
- Scout 拿不到非 evidence 文件列表。

---

## v0.6.0：Quality Mainline

**目标**：解决 DOCX 质量差距暴露的问题。主线是质量，不是功能。

### 必须做

- **`analysis_blocks` 从旁路工具升级为正式 writer contract**：分析师写作时必须遵循结构化分析块模板，而不是自由写全文。
- **强制区分 Fact / Case / Interpretation / Limitation / Action / To Verify**：Claim schema 升级 epistemic type 和 evidence relation 双维度。
- **接入 Issue [#19](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/19)、[#41](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/41)、[#43](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/43)**。
- **RelevanceGate 正式化**：决定哪些 claim 能进正文、摘要、附录或丢弃。
- **DeliveryGate**：检查语言、读者匹配、主题相关性、实体相关性、章节完整性、英文泄漏、通用模板泄漏。

### 验收标准

- Analyst 不输出自由撰写全文，只输出结构化分析块。
- 每份读者交付报告都经过 RelevanceGate + DeliveryGate。
- DOCX/Markdown 输出在弱模型下不会出现章节空洞或模板泄漏。
- 免费/弱模型只做局部受控任务（如提取 claim、写单段分析），不由单个 Prompt 一次写完整周报。

---

## v0.6.1 — v0.6.2：Evaluation And Golden Runs

**目标**：能客观判断"质量有没有变好"。

### 必须做

- **建 5 类 golden workspace**：normal weekly、quiet week、sparse evidence、conflicting sources、feedback contamination。
- **为每类保存 expected artifacts**（不包含真实私有材料）。
- **质量指标**：relevance、claim coverage、unsupported statements、language match、reader depth、DOCX render fidelity。
- **`mabw eval` 命令或 CI golden smoke**：不要求模型输出完全一致，但要求合同和质量门过线。

### 验收标准

- `mabw eval --golden normal_weekly` 可以在 CI 跑。
- 每次 PR 有质量回归信号，不是只看 pytest 通过。

---

## v0.7：Packaging And Distribution

**前提**：v0.5.8 的 package asset 问题已解决。

### 必须做

- **正式支持 curl / PowerShell / Homebrew 的升级路径**。
- **`multi-agent-brief assets install --profile hermes|claude|opencode|codex`**：一键安装运行时适配器到目标平台。
- **`multi-agent-brief assets doctor`**：检查已安装 assets 是否完整、版本是否匹配。
- **Package resources 通过 `importlib.resources` 读取**：不依赖当前目录是 repo root。

### 验收标准

- `curl install.sh | bash` 安装后能 `multi-agent-brief assets install --profile hermes`。
- `assets doctor` 输出完整性和版本检查报告。
- Homebrew formula 指向最新版本且可正常安装。

---

## v1.0：Stable Baseline

**v1.0 不应是 MAS Runtime。** v1.0 应冻结以下基线：

- subagent-first handoff contract
- Hermes primary path
- input governance（inputs classify + hard gates）
- RelevanceGate + DeliveryGate
- golden eval baseline（5 类 workspace + 质量指标）
- package/install story（curl / PowerShell / Homebrew / importlib.resources）
- Support Matrix

### 范围

- **Golden Dataset**：normal weekly、quiet week、sparse evidence、conflicting sources、feedback contamination 五类 public-safe 数据集。
- **Benchmark Metrics**：source count、claim count、citation coverage、unsupported statements、high-risk findings、audit status、runtime、cost、artifact hashes。
- **Contract Compliance Tests**：覆盖 SourceProvider、AnalysisModule、AuditAgent、OutputRenderer、DeliveryConnector。
- **Release Consistency Gate**：package version、CHANGELOG、README、Git tag、agent configs、schema versions、release notes 一致。
- **`v1-maintenance` 分支**：只修 bug、治理漏洞、兼容性和文档，不做大架构重构。

### 完成标准

- v1.0 可以从全新安装跑通正式支持能力。
- v1.0 有稳定接口、基准数据和回归指标。
- v1.0 可作为未来 MAS Runtime 的对照组和回退引擎。

---

## v2.0：MAS Runtime（推迟）

v2.0 不应在 v1.0 前启动为主路径。v1.0 冻结后，可新建 `mas-runtime` / `v2` 分支探索真正 MAS。

建议第一阶段 `mas-runtime-foundation`：

- Shared World / SQLite Event Store
- Typed Event / AgentMessage envelope
- TaskBoard、lease、task bidding 或最小 Contract Net
- AgentState / inbox cursor / capability registry
- ClaimProposal 状态机
- 确定性 ClaimReducer，将 proposal 转为正式 Claim Ledger
- Run replay 与 v1 Claim Ledger 兼容导出

暂不做：

- 不迁移完整 Analyst / Editor / Auditor / Formatter
- 不做多服务器、Kafka、Redis 或复杂部署
- 不把所有 connector 和 analysis module 一次性迁移
- 不把 v2 作为 README 主路径

评估详见 [v2.0 MAS Runtime 重构评估](mas-v2-evaluation.zh-CN.md)。

---

## 暂缓事项

以下事项在 v1.0 前应谨慎控制范围：

- 更多搜索后端和交付渠道
- 完整模型路由
- 完整 RAG / 长期记忆系统
- 大量专题模块
- 调度、多租户、团队权限和企业部署
- 未完成的 PDF / Email / Slack / Telegram 等能力

对于尚未稳定的能力，README 和 CLI 输出必须明确标记为 Experimental 或 Interface Only。
