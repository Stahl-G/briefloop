# 🧾 BriefLoop

**把 AI 写的周报、行业简报和管理层材料，变成可以追问、可以复盘、可以交接的工作流。**

[English](README.md) | [简体中文](README.zh-CN.md)

官网：[briefloop.ai](https://briefloop.ai) · 联系：[contact@briefloop.ai](mailto:contact@briefloop.ai)

[OpenAI Build Week](#openai-build-week-2026) · [15 分钟试用](docs/15-minute-pilot.zh-CN.md) · [Getting Started](docs/getting-started.md) · [Weekly Loop](docs/weekly-loop.md) · [Troubleshooting](docs/troubleshooting.md) · [Reference Workspace](examples/reference-workspaces/industry-weekly-demo/README.md) · [联系方式](docs/contact.zh-CN.md)

写作入口：Claude Code 里用 `/briefloop`；命令行里用 `briefloop`。

---

## OpenAI Build Week 2026

BriefLoop 同时使用了 **Codex** 和 **GPT-5.6**。二者承担不同职责，
并且都不能自行把模型输出升级为项目或报告中的权威事实。

| 参与者 | 如何使用 | 权力边界 |
|---|---|---|
| **Codex** | 作为主要工程环境，参与架构分解、Python 实现、测试、对抗审查、故障分析、文档和有范围的修复。Codex 也是 BriefLoop 当前 Experimental SQLite runtime 路径的宿主。 | Codex 可以提出并实现有边界的变更，但不能批准自己的工作、决定合并，也不能自行授权产品或研究主张。 |
| **GPT-5.6** | 用于生成受控对照中的候选声明和行业周报草稿。在 Codex 中，GPT-5.6 Sol 的最高推理强度配合 Academic Research Skill，用于相关工作研究、候选实验设计和技术报告起草；独立的 GPT-5.6 Sol Pro 会话用于挑战对照条件、可证伪标准、引用和主张边界。 | 模型输出始终只是提案，不会自动成为来源证据、已接受的实验结果或可公开发布的结论。 |
| **人类维护者** | 选择研究问题、核验一手来源、定义不变量和验收标准、冻结实验协议、接受或拒绝修订，并批准合并与发布。 | 最终架构、实验、风险接受、合并和发布权始终由人类掌握。 |

Academic Research Skill 用于组织研究流程，本身不被当作证据。真正引用
的来源仍然是论文、官方文档、仓库工件和一手发布材料。

### 评委快速试用——不需要 API Key

需要 Python 3.12。

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
bash scripts/demo.sh
```

该确定性 Demo 会生成一套 public-safe 参考工作区，包括读者简报、
Claim Ledger、Quality Panel、来源附录和事件日志摘录。它不会调用模型、
抓取实时来源，也不证明输出质量。

* [英文技术报告 v0.6.1](https://briefloop.ai/reports/briefloop-architecture-reference-v0.6.1.en.html)
* [15 分钟试用](docs/15-minute-pilot.zh-CN.md)
* [公开参考工作区](examples/reference-workspaces/industry-weekly-demo/README.md)

### 证据边界

Prompt、Skill 和 BriefLoop 的对照结果，只能依据已经冻结的工件、哈希
和完成的审阅记录进行报告。本 README 不宣称 BriefLoop 已经赢得对照、
能够自动解决所有知识冲突、证明语义真值或取代人工审查。

---

## ✨ 一句话说明

BriefLoop 是一个开源的简报工作流工具。

它不是“让 AI 多写一点”的 prompt，而是帮你把一份周期性简报背后的过程记清楚：

- 这段话用了哪些来源？
- 这个数字是哪来的？
- 哪些检查通过了，哪些没通过？
- 谁批准了哪些读者偏好？
- 下次怎么少犯同样的错？

> 当有人问“这个数字哪来的？”BriefLoop 不让模型临场编理由，而是打开账本。

---

## 🧯 它解决什么问题？

很多人每周都要写类似的材料：

- 行业周报
- 市场动态
- 竞品跟踪
- 政策简报
- 投研材料
- IR / 管理层汇报
- 项目进展简报

AI 可以很快写出一篇“看起来像真的”的报告，但问题也很明显：

1. **来源容易丢。**
   数字、日期、公司名称进入终稿后，过几天很难说清楚它来自哪里。

2. **错误容易扩散。**
   一个弱来源、一句误读、一个过期数据，可能在多轮改写后变成非常自信的结论。

3. **反馈留不下来。**
   领导说“下次先讲结论”“不要泛泛而谈”“这个行业必须核原始公告”，如果只靠人脑记，下一次很容易重犯。

4. **新人很难接手。**
   简报怎么写、什么不能写、什么必须查，通常藏在某个人的经验里，而不是在流程里。

BriefLoop 的目标就是把这些东西变成可记录、可检查、可复用的流程。

---

## 👥 谁适合看这个项目？

BriefLoop 适合：

- 每周要写行业周报、市场简报、竞品跟踪或管理层材料的人；
- 战略、市场、投研、IR、总裁办、研究助理等需要长期跟踪信息的团队；
- 想把 AI 简报从“能写”推进到“能被追问”的团队；
- 研究 agent workflow、human-in-the-loop、可审计 AI 流程的人。

它暂时不适合：

- 只想要一个“一键生成漂亮报告”的工具；
- 希望 AI 自动判断真伪、自动发布、自动替你承担责任的人；
- 想把外部 AI 已经写好的报告丢进来，然后让系统证明它完全正确的人。

---

## 🧱 BriefLoop 实际做了什么？

你可以把它理解成一条“有账本的简报流水线”。

| 步骤 | 它做什么 | 为什么有用 |
|---|---|---|
| 1. 准备材料 | 整理本地材料、搜索结果或来源包 | 避免模型一开始就凭空写 |
| 2. 登记事实 | 把关键数字、日期、实体、主张写入 Claim Ledger | 以后可以查“这句话从哪来” |
| 3. 分工写作 | Scout / Analyst / Editor / Auditor 等角色按边界协作 | 写作不是一坨 prompt，而是分阶段处理 |
| 4. 质量检查 | 用质量门禁检查新事实、过期来源、缺失来源、交付状态 | 能用规则检查的东西，不交给模型记忆 |
| 5. 人工交付 | 最终交付必须由人触发 | 系统不自动发布、不绕过人 |
| 6. 查看审计轨迹 | 已接受动作留下 SQLite receipt；JSON、Markdown、HTML 只是可替换投影 | 可审计不等于产生第二套运行时权威 |

一句话：**Agent 负责写和提议；确定性服务接受权威 effect；最终交付仍由人触发。**

---

## 📚 每周它替你记住四件事

| 问题 | BriefLoop 记录什么 | 常见位置 |
|---|---|---|
| 这次简报做到哪了？ | 当前 Store revision、阶段、阻塞原因和下一步动作 | `briefloop status`、`briefloop runtime next`、SQLite ControlStore receipt |
| 每个数字哪来的？ | Claim Ledger、来源日期、来源附录、质量门禁结果 | `claim_ledger.json`、`source_appendix.md`、`quality_gate_report.json` |
| 哪些动作真正生效了？ | 被接受的 strict request、transaction receipt 和 invocation lineage | 通过受支持的 status/runtime view 查看 `briefloop.db` |
| 什么在替你把关？ | Store-backed gate evaluation、package readiness 和显式人工批准 | Receipt-backed runtime action 与只读状态投影 |

Agent 可以观察和提议；只有通过严格校验并被确定性服务接受的请求才会改变 Store，交付仍由人控制。当前尚未交付 Store-native 的可复用 guidance 或 Improvement Ledger。

---

## 📦 你最终会拿到什么？

一次正常运行后，真正给读者看的交付稿通常是：

- `output/delivery/brief.md`
- `output/delivery/<report-name>.docx`

对于 fresh Codex run，workspace 内的 `briefloop.db` 是唯一运行时权威。
根据当前阶段，BriefLoop 还可能生成可替换的审计或读者投影，例如：

- `output/intermediate/claim_ledger.json`：关键事实和来源登记；
- `output/source_appendix.md`：来源附录；
- `output/intermediate/quality_gate_report.json`：质量门禁结果；
- `output/intermediate/quality_panel.html`：只读质量面板。

这些投影不是运行时合法性的读取来源，也不是都需要普通读者查看；它们用于追问、复盘、交接和排错。

---

## 🔎 一个很小的例子

终稿里可能出现一句话：

```markdown
本周示例光伏组件现货均价环比下降 1.8%，为连续第三周回落。
```

BriefLoop 不希望这句话孤零零地躺在报告里。它应该能在事实账本里找到对应记录：

```json
{
  "claim_id": "CL-0012",
  "statement": "示例组件现货均价环比下降 1.8%",
  "source_id": "SRC-003",
  "evidence_text": "示例来源摘录，显示组件价格环比变化。",
  "metadata": {
    "published_at": "2026-06-05",
    "source_title": "示例光伏价格表"
  }
}
```

如果来源过期、数字没有登记、编辑阶段新增了未经登记的事实，质量门禁应该把问题暴露出来，而不是让它悄悄进入终稿。

---

## 🚀 快速开始

### macOS / Linux

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
```

如果只是想先看一个 API-free demo，走
[15 分钟试用](docs/15-minute-pilot.zh-CN.md)。

Package-index 安装还不是当前 launch 路径。`pipx` / PyPI 打包准备记录在
[pipx And PyPI Packaging Prep](docs/packaging-pipx.md)；除非 release notes 明确说明
真实 package-index artifact 已发布并通过 smoke，否则不要使用 `pipx install briefloop`
作为安装指令。

创建第一份简报工作区：

```bash
briefloop onboard
briefloop init ~/briefloop-workspace --from-onboarding onboarding.json
briefloop runtime install --workspace ~/briefloop-workspace --runtime codex
briefloop run --workspace ~/briefloop-workspace --runtime codex
```

常用设置辅助命令：

```bash
briefloop init --from-onboarding onboarding.json <workspace>
briefloop runtime install --workspace <workspace> --runtime codex
briefloop run --workspace <workspace> --runtime codex
```

如需使用一组冻结的本地材料，runtime 接受一份包含 1–256 个成员的严格
source-pack request，并在一个 SQLite Receipt 中原子登记全部成员，不会静默只取
第一份文件。request 同时绑定冻结 manifest 的哈希，并保留其中的 source ID、原始
URL 与事件 `opened_at` 元数据。workspace 内的 Codex kit 也会被哈希绑定；修改、删除、增加角色文件
或使用符号链接都会在继续运行前 fail closed。

旧的 `briefloop sources decide` 仅作为退役命令名保留；SQLite Codex run 的来源
登记必须走 Store-derived runtime action，不得把旧 JSON 路径当作 fallback。

### Windows PowerShell

Windows 不需要 WSL 或 Git Bash。推荐直接用 PowerShell。

```powershell
winget install Python.Python.3.12

git clone https://github.com/Stahl-G/briefloop.git
cd briefloop

.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1

briefloop version
```

如果 PowerShell 拦截脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

---

## 🤖 运行时迁移

新建运行的当前入口是 fresh SQLite-only Codex。先创建 workspace，再安装
packaged runtime kit 并启动 Codex runtime：

```bash
briefloop onboard
briefloop init ~/briefloop-workspace --from-onboarding onboarding.json
briefloop runtime install --workspace ~/briefloop-workspace --runtime codex
briefloop run --workspace ~/briefloop-workspace --runtime codex
```

新运行只把 SQLite `briefloop.db` 作为运行时权威。JSON-only workspace 不会
被导入、迁移或作为控制输入。Codex adapter 仍是 Experimental；JSON、
Markdown、HTML、status 和 Quality Panel 都只是可替换投影。

原 Claude/Hermes/OpenCode/operator JSON-control 路径不是新 SQLite run 的
入口，也不能用来继续或迁移 JSON-only workspace。

建议新用户先看：

- [15 分钟试用](docs/15-minute-pilot.zh-CN.md)
- [Getting Started](docs/getting-started.md)
- [Weekly Loop](docs/weekly-loop.md)
- [Troubleshooting](docs/troubleshooting.md)

---

## 🧪 三条上手路径

先按报告类型选择产品入口：

| 报告任务 | 选择入口 | 适合场景 |
|---|---|---|
| 行业 / 市场周报 | `industry-weekly` | 行业动态、竞品跟踪、政策监测 |
| 管理层月报 | `management-monthly` | 管理层复盘、月度经营更新、汇报材料 |
| 文档审阅 | `document-review` | 对一组文档做有页码 / 来源 trace 的审阅 |

```bash
briefloop new industry-weekly ./weekly-brief
briefloop new management-monthly ./monthly-review
briefloop new document-review ./document-review
```

`document-review` 是文档证据审阅工作区入口，不代表自动法律、合规或披露判断。

新用户先从上面三个支持入口开始。其他实验性入口在后文说明，适合已经理解
基本循环的用户。

| 路径 | 适合谁 | 怎么做 |
|---|---|---|
| 看一眼 | 想判断这个项目是不是有意义 | 跑 demo，读公开运行摘要 |
| 跑一次 | 想用少量本地材料试一次 | 建 workspace，放材料，跑一份简报 |
| 每周使用 | 想把它变成固定工作流 | 配置来源、栏目、读者偏好和反馈流程 |

可选 demo：

```bash
bash scripts/demo.sh
bash scripts/demo-deep-dive.sh
```

demo 用的是合成材料，主要用来展示证据链和门禁行为。真实使用应该从你自己的材料和 workspace 开始。

---

## 🧭 当前状态

当前版本：**v0.14.0**

当前主要入口：

- CLI：`briefloop`
- Experimental SQLite-only Codex runtime：`briefloop run --workspace <path>
  --runtime codex`，随后使用 `briefloop runtime next`、
  `invocation-start`、`invocation-accept|fail` 和 `apply`
- Experimental 一次性网页初始化：`briefloop init <path> --web`
- 只读三页报告视图：`briefloop quality html --workspace <path> [--open]`
- 实验性 offline-shadow LAJ：`briefloop experiments laj shadow-run` 与
  `briefloop experiments laj present`；仅用于公开/合成材料的 advisory 评估及
  独立 JSON/Markdown/HTML 展示；也可通过
  `briefloop quality summarize --laj-view <laj.json>` 只读展示显式提供且与当前
  报告绑定的 `laj.json`

v0.14.0 完成 SQLite-only 切换，并增加只读交互面：

- SQLite ControlStore（`briefloop.db`）、已接受的 strict request、Receipt 与
  ledger relation 是唯一运行时权威。JSON-only workspace 不受支持；没有导入、
  迁移、dual read/write 或 fallback。
- legacy JSON runtime-state 栈及其 dead consumer 已删除。legacy control file
  与 report/status/Quality Panel export 都是非权威投影；strict action、envelope
  和 human-request JSON payload 必须由 Store 重验，本身不是权威。
- 打包的 Codex Skill 只执行 Store 派生的精确下一动作与 Receipt-backed
  invocation，不回退到 `operator` 或其他 runtime。
- loopback init wizard 与三页 HTML 都是只读交互面。LAJ 仍为 Experimental，
  效用 NOT MEASURED；Improvement Ledger 页面如实显示 unavailable，不能把
  guidance 写入下一 run。
- v0.14 工程改动由 Codex 实现和测试；人类维护者授权合并与发布。

延续的受支持报告工具与 advisory quality surface 包括：

- `ReportSpec`、`ReportPack`、`ReportTemplate` 和 `PolicyProfile` contract
- workspace skeleton 和确定性的 PolicyProfile 解析
- delivery / audit bundle manifest 与干净 bundle archive
- 支持的 `industry-weekly`、`management-monthly` 和 `document-review` 产品入口
- 有边界的 `evidence_extract` source/scope 注册、source lock、logical page
  inventory seed 和 text-span seed registry
- 实验性的 SourceHub Lite 来源设置入口，用于本地文件、RSS feed 和 runtime web-search handoff task
- durable source evidence pack materialization 和 source taxonomy normalization
- 内部 review release-mode approval record
- Quality Panel JSON / Markdown / HTML 投影及 audit bundle 集成
- 独立的实验性 LAJ JSON / Markdown / HTML second-opinion artifact，以及显式绑定
  view 的可选只读 Quality Panel 区域；它不改变 workflow status、Gate、finalize、
  delivery、repair、approval、权威建议动作或 next-action authority，且 evaluator
  efficacy 尚未测量
- reader-quality warning / projection surface：template conformance、
  materiality selection、support-calibrated wording、citation profile、
  coverage/omission 和 scoped final-abstract diagnostics
- repeated retry / repair / blocker loop 的 trajectory-regulation decision
  narrowing
- proposal-only Semantic Support Auditor surface 和 human adjudication record；
  这些记录不创建 support truth、gate、delivery approval 或 release authority
- 公开安全的 reference、synthetic regression、minimal comparative evaluation、
  launch smoke 和 release checklist guardrail

这些功能的定位是：**让报告类型、来源证据、默认策略、交付包和 operator quality visibility 更产品化**。

但这些功能仍然只是 contract、元数据、默认配置、设置入口、审批记录、确定性 warning
和投影控制，不代表系统已经能自动解析 PDF、执行隐藏搜索或爬取、判断行业合规、投资建议、披露可用性、语义真实性，或授权公开发布。

---

## 🚧 它不是什么？

BriefLoop 现在明确不做这些事：

- 不自动发布报告；
- 不绕过人工审核；
- 不保证来源语义上支持每个子主张；
- 不替代法律、合规、投资或披露判断；
- 不声称生成内容可以直接用于 IR / SEC / 监管披露；
- 不把未批准反馈变成长期记忆；
- 不承诺“一键生成最终正确报告”。

更准确地说，BriefLoop 当前的核心承诺是：

> **Traceability, not semantic proof yet.**
> 先做到可追踪、可复盘、可问责；语义级证明和自动判断仍是后续方向。

---

## 💡 为什么做这个项目？

写代码的世界有测试、CI、Git history 和 code review，所以 coding agent 的进步很快。

但商业简报、行业周报、投研材料、管理层汇报通常没有这种基础设施。很多错误靠人肉复核，很多反馈靠口头传达，很多经验靠某个熟手记住。

BriefLoop 想把软件工程里的那套“可追踪、可回滚、可审计、可测试”的思想搬到简报工作里。

它的目标不是让人不思考，而是让人把时间花在判断、追问和决策支持上，而不是反复搬运、排版和修同样的错。

---

## 📖 术语表

| 术语 | 英文 | 人话解释 |
|---|---|---|
| 事实账本 | Claim Ledger | 记录关键事实、数字、来源和日期 |
| 来源包 | Source Pack | 本次运行可用的材料集合 |
| 质量门禁 | Quality Gate | 进入下一阶段或交付前必须通过的检查 |
| 控制存储 | ControlStore | fresh Codex run 的权威 SQLite 状态 |
| 事务回执 | Transaction Receipt | 确定性记录一次已接受的 Store action |
| 司乐师 | Orchestrator | 调度各角色、维持流程边界的运行时角色 |
| 交付包 | Delivery Bundle | 给读者看的 Markdown / Word 文件 |
| 审计材料 | Audit Artifacts | 给复盘、排错、追问用的中间记录 |

---

## 🗂️ 文档入口

新用户先看：

- [入门指南](docs/getting-started.md)
- [每周工作流](docs/weekly-loop.md)
- [故障排查](docs/troubleshooting.md)
- [黄金参考工作区](examples/reference-workspaces/industry-weekly-demo/README.md)

架构参考和贡献者文档：

- [功能地图](docs/features.zh-CN.md)
- [黄金路径](docs/golden-path.zh-CN.md)
- [WorkBuddy 指南](docs/workbuddy.zh-CN.md)
- [我每周怎么用 BriefLoop](docs/weekly-use.zh-CN.md)
- [架构状态](docs/architecture-status.zh-CN.md)
- [路线图](docs/roadmap.zh-CN.md)
- [红线与反模式](docs/red-lines-and-anti-patterns.md)
- [Product OS 读者质量 reference package](docs/reference-runs/v0.11.3-product-os-reader-quality-reference.md)
- [最小对照评估包](docs/evaluation-results/v0.11.4-minimal-comparative-evaluation/README.md)
- [合成回归包](docs/reference-runs/v0.11.1-synthetic-regression-pack.md)
- [公开运行摘要](docs/reference-runs/v0.7.2-public-solar-integration.zh-CN.md)
- [失败研究](docs/reference-runs/v0.7.4-organoid-failure-study.md)
  ([中文](docs/reference-runs/v0.7.4-organoid-failure-study.zh-CN.md))

---

## 🤝 合作

这个项目最需要的不是更多概念，而是真实场景。

欢迎这些人参与：

- 每周真实写行业周报、市场简报、IR 材料、管理层材料的人；
- 想用真实工作流试点 BriefLoop 的团队；
- 研究 agent evaluation、human-in-the-loop、可审计 AI 工作流的人；
- 愿意从 issue、文档、测试、示例场景开始贡献的人。

可以从 [good first issue](https://github.com/Stahl-G/briefloop/issues) 开始。提交前建议先读 [红线与反模式](docs/red-lines-and-anti-patterns.md)。

人工联系入口见 [briefloop.ai](https://briefloop.ai) 和
[联系方式](docs/contact.zh-CN.md)。私密安全问题请发到
[security@briefloop.ai](mailto:security@briefloop.ai)。

---

## 📄 License

MIT
