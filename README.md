# Multi-Agent-Brief-Workflow

**MABW：Process Accountability for the Briefing Loop**

**MABW：可问责的 AI 简报工作流**

<p align="center">
  <a href="README_en.md">English</a> |
  <a href="README.md">简体中文</a>
</p>

一个基于来源、可审计、可由 AI agent 协作执行的简报工作流，用于生成商业、研究、市场、政策、公司跟踪和管理层汇报材料。

> 它会观察、会提议；但只有你点头的，才会被记住，而且记在一本你随时能翻、能撤销的账上。

`Multi-Agent-Brief-Workflow` (MABW)不是一个"AI 写周报"的 Prompt。它把真实工作中的 briefing 流程拆成受契约约束的步骤：理解需求 → 发现来源 → 整理材料 → 建立事实账本 → 辅助写作 → 审计校验 → 输出文档。每一步产出什么文件、由谁产出、何时可以进入下一步，都有明确定义并留有完整记录。

本项目不是投资建议工具，不是交易信号生成器，也不能替代人工审核。

## 当前状态（诚实版）

当前版本：**v0.7.4**

* **能跑的**：subagent-first 工作流（Hermes / Claude Code / Codex / OpenCode），运行时状态文件，事实账本，确定性质量门禁，反馈与修复计划，溯源投影，受众画像快照，受控的改进账本 / 改进记忆，Markdown / Word 输出。1000+ 确定性测试在 CI 中通过，不依赖任何 LLM 调用。
* **v0.7.4 新增**：在 v0.7.3 的 release safety hardening 之上，补齐 stale audit report 阻断、来源附录交付语义收口、release public-safety 检查接线，并公开一份类器官行业研究失败研究，明确 source-to-claim 语义支撑仍是 v0.8 目标。
* **还不是的**：不是自治 agent，不会自动修改简报内容，不会自动学习，没有长期记忆系统。详见 [当前架构状态](docs/architecture-status.zh-CN.md) 和 [路线图](docs/roadmap.zh-CN.md)。

设计原则一句话：**系统提案，人类决定。** 全部红线见 [docs/red-lines-and-anti-patterns.md](docs/red-lines-and-anti-patterns.md)。

## 每周它替你记住四件事

MABW 的用户心智模型不是“有多少个控制面”，而是每次简报运行时它替你守住四件事：

| 问题 | 它记录什么 | 你在哪里看 |
|---|---|---|
| 本期写到哪了 | 当前 stage、缺失产物、阻塞原因和下一步安全动作 | `/mabw status`、`workflow_state.json`、`agent_handoff.md` |
| 每个数字哪来的 | Claim Ledger、来源日期、审计和质量门禁结果 | `claim_ledger.json`、`quality_gate_report.json`、`source_appendix.md` |
| 它学到了什么 | 只有人工批准的读者偏好；未批准建议不会生效 | `improvement/ledger.jsonl`、`improvement_memory_snapshot.md` |
| 什么在替你把关 | 阶段完成事务、reader-final gate、来源附录和交付检查 | `finalize_report.json`、`reader_clean`、`state finalize-complete` |

一句话：AI 可以写草稿；系统记录账本；只有你能让偏好影响后续运行。面向业务用户的解释见 [docs/what-mabw-keeps-track-of.zh-CN.md](docs/what-mabw-keeps-track-of.zh-CN.md)。

## 为什么做这个项目

在企业战略部、券商研究所、基金投研、投资者关系、总裁办等场景中，很多人花大量时间制作日报、周报、晨会材料和领导层简报。这些工作重要，但流程高度重复：找来源、判断取舍、去重去旧、整理成文、核对数字出处、检查 AI 有没有编造、改措辞、排版输出。

更深一层的问题是：**这类工作无法系统性地变好。** 新人犯的错被口头纠正然后被遗忘，下一个新人重犯；"这段感觉不对"的反馈在会后蒸发；一个过期数字混进简报，没人能追溯它是在哪一步漏掉的。

写代码的世界靠测试、Git 历史、CI 和 code review 形成了改进闭环，所以 coding agent 进步飞快。本项目把同一套基础设施——可审计、可追溯、结构化反馈、人类把关——搬进真实的简报工作流。让人把时间花在判断、提问和决策支持上，而不是重复搬运和排版。

### 为什么叫「司乐师」？

英文 orchestrator 来自管弦乐编配与协调的语境，在软件工程中常译为“编排器”。MABW 选择译作「司乐师」：它不直接替各个角色写作，而是调度信息侦察员、筛选师、分析师、编辑师和审计师，让不同声部按契约合奏。

「司乐」也借用了中国礼乐传统中掌管乐政、乐教的意象。这里不是对古代官职的严格复原，而是一个项目术语：负责维持节奏、边界、秩序和交付。

## 它解决什么问题

AI 生成报告的常见问题不是"写得不够快"，而是：

* 不知道一句话的来源在哪里；数字和日期容易丢失出处；
* 多轮修改后引用关系断掉；
* 来源太多，重复、过期、低质量信息混在一起；
* Prompt 一长，模型容易跳步骤；
* 最终文档看起来完整，但无法审计。

本项目的回答是一条受契约约束的流水线：

```text
用户需求 → 来源发现 → 来源治理 → 事实账本 → Agent 辅助写作 → 审计与门禁 → Markdown / Word 输出
```

每个阶段由专职子代理执行（信息侦察员 → 筛选师 → 事实账本 → 分析师 → 编辑师 → 审计师），由"司乐师"（Orchestrator）统一调度，所有状态落在可检查的文件里。详细架构见 [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md)，完整技术报告见 [docs/mabw-architecture-reference-v0.1.2.md](docs/mabw-architecture-reference-v0.1.2.md)。

## 看一眼产出长什么样

最终交付包只放 `output/delivery/brief.md` 和 `output/delivery/<命名>.docx`。配置来源附录时，来源列表会追加在这两份交付稿底部；独立的 `output/source_appendix.md`、Claim Ledger、audit report 和 audited brief 继续保留为审计追溯文件，不作为额外交付文件。下面是一个**合成示例**（虚构主体，仅展示结构）。真实运行证据见 [v0.7.2 公开光伏集成运行摘要](docs/reference-runs/v0.7.2-public-solar-integration.zh-CN.md)；它是 integration reference，不是输出质量提升或严格因果效果证明。另见 [v0.7.4 类器官行业研究失败研究](docs/reference-runs/v0.7.4-organoid-failure-study.zh-CN.md)：一次真实外部课题如何暴露 source-to-claim 语义支撑边界。

`output/delivery/brief.md`（节选）：

```markdown
## 二、市场动态
本周示例光伏组件现货均价环比下降 1.8%，为连续第三周回落。
N 公司宣布其示例州工厂一期产线于本周投产，规划年产能 2GW……
```

`output/intermediate/claim_ledger.json`（对应条目，节选）：

```json
{
  "claim_id": "CL-0012",
  "statement": "示例组件现货均价环比下降 1.8%",
  "source_id": "SRC-003",
  "source_date": "2026-06-05",
  "support": "supported"
}
```

`output/intermediate/quality_gate_report.json`（节选）：

```json
{
  "gate_id": "freshness",
  "status": "pass",
  "findings": []
}
```

也就是说，按契约运行时，成稿里的关键数字应能在事实账本里找到登记的来源和日期；过期来源、无出处数字应被审计和质量门禁暴露出来，而不是无记录地混进终稿。审计轨迹（谁在哪一步做了什么决定）保存在 `event_log.jsonl`。

当前边界也很明确：MABW 提供过程问责和来源可追溯，重要主张会链接到登记过的来源条目，并保留来源、日期和门禁记录。它还不是语义证明系统；这些链接说明“这条主张从哪里进入流水线”，不自动证明来源逐字支持每个子主张。source-to-claim 语义支撑校验是 v0.8 的头号评估目标。

## 三条上手路径

MABW 没有“轻量版”。降低的是进入成本，不降低的是信任标准：事实账本、门禁、人工交付、运行轨迹和冻结快照仍然在场。

| 路径 | 适合谁 | 怎么做 | 不降低什么 |
|---|---|---|---|
| 看一眼 | 想先判断这个项目是不是有意义 | 读 [公开运行摘要](docs/reference-runs/v0.7.2-public-solar-integration.zh-CN.md)，跑 `bash scripts/demo.sh` 和 `bash scripts/demo-deep-dive.sh` | demo 展示的是控制行为和可追问性，不声称输出质量提升 |
| 跑一遍 | 想用几份本地材料试一次 | 不配搜索后端，只放少量本地文本材料，按 [黄金路径](docs/golden-path.zh-CN.md) 走 `new → run → status → deliver` | Claim Ledger、gates、reader-final gate 和人工 deliver 仍然执行 |
| 过日子 | 想每周稳定使用 | 配置搜索后端、来源节奏、feedback 和已批准偏好，按 [每周使用脚本](docs/weekly-use.zh-CN.md) 运行 | 未批准偏好不会生效，已批准偏好只在后续 run 冻结 |

不要把外部 AI 报告直接丢进来“审计”当作轻量入口。没有 Claim Ledger 的外来稿只能做浅层检查，不能提供 MABW 的核心问责能力。

## 开荒一个新行业

MABW 适合把一个行业从“一次性调研”转成“长期、可追踪、可反馈修正的监控流程”。如果你要跟踪一个新赛道，例如类器官、AI 电力需求、储能供应链或新政策主题，建议这样开始：

1. **先做一次开荒研究**：用 Deep Research、人工研究或行业专家访谈建立初始地图，包括核心问题、监管机构、公司宇宙、产品类型、关键词、数据库、常用媒体和需要持续跟踪的事件类型。
2. **不要把开荒报告当作事实来源**：它只能作为 source universe、watchlist 和分类框架的草稿。后续简报里的关键事实仍必须回到原始公告、监管文件、公司新闻、投融资披露、论文或可信媒体。
3. **把行业地图转成 workspace 配置**：把政策、公司/产品、投融资、商业化信号等栏目写入 `user.md` / onboarding 配置，把常用来源、关键词和公司名单整理成可复用 watchlist。
4. **先按周跑，不急着日报化**：第一阶段用 MABW 每周处理新增信息，去重、筛旧、建立 Claim Ledger、生成来源附录，并记录哪些信息真正影响判断。
5. **用反馈修正口径**：当你发现“先讲影响再讲背景”“不要替管理层下决策”“某类数据必须核原始来源”这类稳定要求，先记录为反馈，再由人工批准进入 Improvement Ledger 或后续模板/门禁。
6. **稳定后再提高频率**：只有当来源池、栏目结构、读者偏好和门禁规则稳定后，再把周报拆成日报、预警或专题跟踪。

一句话：Deep Research 适合开荒，MABW 适合长期监控。行业研究不是一次性“多搜一点”，而是一个持续的信息治理流程。

## 快速开始

### Claude Code（五动词主路径）

```bash
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
bash scripts/setup.sh
source .venv/bin/activate

multi-agent-brief claude install --repo-workdir .
```

然后在 Claude Code CLI 或 Claude Desktop Code tab 中使用五个 writer 动词：

```text
/mabw new
/mabw run <workspace>
/mabw status <workspace>
/mabw feedback <workspace> [text-or-file]
/mabw deliver <workspace>
```

`/mabw` 是 writer-facing 入口；完整 delegated subagent workflow 仍由 `/generate-brief <workspace>` 执行。`status` 调用只读的 `multi-agent-brief status`，`feedback` 只记录和分诊，`deliver` 必须经过 gates、reader-final gate 和 `state finalize-complete`。

详细流程见 [docs/claude-code-quickstart.md](docs/claude-code-quickstart.md)。中文写作者可直接看 [MABW 黄金路径](docs/golden-path.zh-CN.md) 和 [我每周怎么用 MABW](docs/weekly-use.zh-CN.md)。

### 其他 runtime

```bash
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ../mabw-workspace --runtime claude
```

Claude Code 是 first-class writer / five-verb path。Hermes 仍是 supported delegated / scheduled runtime path。OpenCode、Codex 和 manual fallback 保留各自现有入口。

Hermes 插件仍可用于 `delegate_task` 原生路径：

```bash
multi-agent-brief hermes install-plugin
hermes plugins enable mabw
```

运行时安装细节、workspace-local kit、常见问题见 [docs/claude-code-quickstart.md](docs/claude-code-quickstart.md) 和 [docs/runtime-recipes.md](docs/runtime-recipes.md)。

### 使用自己的材料 / 启用可选能力

* 导入本地资料与输入分类：见 [docs/onboarding.md](docs/onboarding.md)
* Web 搜索选项（runtime 自带搜索，或 Tavily/Exa/Brave 等 API 增强）：见 [docs/search-backends.md](docs/search-backends.md)
* 源发现候选合并（包括 `llm_decide` source profile）：`multi-agent-brief sources decide --config <workspace>/config.yaml --merge`
* 飞书集成（采集 + 推送）：见 [docs/feishu-integration.md](docs/feishu-integration.md)
* SEC Filing 解析：见 [docs/opencli-source-provider.md](docs/opencli-source-provider.md)
* Windows PowerShell：见 [docs/windows-powershell.md](docs/windows-powershell.md)

常用命令片段：

```bash
multi-agent-brief init --from-onboarding onboarding.json
multi-agent-brief sources decide --config <workspace>/config.yaml
```

## 记录一个已批准的读者偏好

v0.7.0 增加了受控的 Improvement Ledger / Improvement Memory。它用于保存人工撰写、人工批准的读者偏好，例如"证据支持时，先给出决策相关数字"。它不是自动学习系统，也不会自动改稿。

```bash
multi-agent-brief improve propose --workspace <workspace> \
  --guidance "Lead with the decision-relevant number when evidence supports it." \
  --category audience_mismatch \
  --scope brief \
  --source-summary "Operator-created audience guidance proposal."

multi-agent-brief improve approve --workspace <workspace> --entry-id AG-0001 --by <operator>
multi-agent-brief improve rebuild --workspace <workspace>
multi-agent-brief run --workspace <workspace> --skip-doctor
```

`approve` 不会改变已经创建的当前 run snapshot；下一次 `run` / `start` / `handoff` 才会把已批准偏好冻结为 runtime 可读 snapshot。MABW 可以证明该 snapshot 是否被生成、记录并交给 runtime；最终文本是否体现这些偏好仍需要单独评估。运行时只读取 `output/intermediate/improvement_memory_snapshot.md`，不把 `improvement/memory.md` 当作实时输入。详细说明见 [docs/modules/improvement.md](docs/modules/improvement.md)。

## 寻找合作 🤝

这个项目由一名制造业从业者在真实简报工作中开发和使用。它现在最需要的不是更多功能，而是更多真实场景。如果你符合以下任何一类，欢迎联系（GitHub Issue / Discussion 均可）：

* **试点用户**：你在战略、投研、IR、总裁办、研究所等岗位，每周真实地写行业周报、竞品跟踪或管理层简报，愿意用它跑自己的真实流程并反馈摩擦点。我们会优先支持试点场景的问题。
* **评估合作者**：你在高校或研究机构做 LLM agent / 多智能体系统方向，对"契约治理的工作流 vs 单模型基线"的对照实验、消融实验感兴趣。系统、真实场景和运行数据由项目方提供。
* **贡献者**：从一个 [good first issue](https://github.com/Stahl-G/multi-agent-brief-workflow/issues) 开始即可；提交前请读 [红线与反模式](docs/red-lines-and-anti-patterns.md)。

## 术语表（Glossary）

| 中文术语 | English | 说明 |
|---|---|---|
| 司乐师 | Orchestrator | 运行时主智能体，负责调度、检查、决策和交付门禁 |
| 事实账本 | Claim Ledger | 登记关键事实主张及其证据来源 |
| 运行交接单 | Runtime Handoff | 向不同 agent runtime 交付执行上下文和契约引用 |
| 产物契约 | Artifact Contract | 定义每个阶段应产生、消费和验证的文件 |
| 质量门禁 | Quality Gate | 在进入下一阶段或定稿前执行的质量检查 |
| 溯源图 | Provenance Graph | 从运行状态、产物、事实、反馈和门禁派生的审计图 |
| 控制台 | Control Switchboard | 记录可用控制项、建议和司乐师选择 |
| 信息侦察员 / 筛选师 / 分析师 / 编辑师 / 审计师 | Scout / Screener / Analyst / Editor / Auditor | 各阶段专职子代理 |

## 路线图（摘要）

* **v0.7**：改进账本（Improvement Ledger）——把人工撰写、人工批准的读者偏好按运行冻结为 Improvement Memory snapshot；不做自动学习、FrictionStore 自动检测或输出质量承诺。
* **v0.8**：评估实验与策略包——定义 guidance manifestation / regression 评估协议，对照单模型基线，并推进 mode registry 与第二个 policy pack。
* **v0.9**：分发与参考工作流——零 API key 快速上手、参考运行、文档整顿。
* **v1.0**：稳定基线——schema 冻结、CLI 表面冻结、安全威胁模型、明确支持边界。

完整版见 [docs/roadmap.zh-CN.md](docs/roadmap.zh-CN.md)；已实现 vs 目标的区分见 [docs/architecture-status.zh-CN.md](docs/architecture-status.zh-CN.md)。

## 文档索引

[架构](docs/architecture.zh-CN.md) ·
[技术报告 v0.1.2](docs/mabw-architecture-reference-v0.1.2.md) ·
[司乐契约模型](docs/orchestrator-contracts.zh-CN.md) ·
[质量门禁](docs/harness.md) ·
[评估用例](docs/evaluation-cases.md) ·
[改进账本](docs/modules/improvement.md) ·
[黄金路径](docs/golden-path.zh-CN.md) ·
[每周使用脚本](docs/weekly-use.zh-CN.md) ·
[公开运行摘要](docs/reference-runs/v0.7.2-public-solar-integration.zh-CN.md) ·
[发布验证清单](docs/launch-validation.zh-CN.md) ·
[支持矩阵](docs/support-matrix.md) ·
[安全](docs/security.md) ·
[迁移说明](docs/MIGRATION.zh-CN.md)

## License

MIT
