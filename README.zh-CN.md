# 🧾 BriefLoop

**把 AI 写的周报、行业简报和管理层材料，变成可以追问、可以复盘、可以交接的工作流。**

原名：**MABW — Multi-Agent Brief Workflow**。
现在的项目名是 **BriefLoop**；MABW 保留为历史名称、实现血统和兼容入口。

[English](README.md) | [简体中文](README.zh-CN.md)

[功能地图](docs/features.zh-CN.md) · [黄金路径](docs/golden-path.zh-CN.md) · [架构状态](docs/architecture-status.zh-CN.md) · [路线图](docs/roadmap.zh-CN.md)

写作入口：Claude Code 里用 `/briefloop`（`/mabw` 仍是兼容别名）；命令行里用 `briefloop` 或 `multi-agent-brief`。

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
| 6. 反馈沉淀 | 只有人工批准的读者偏好才会进入后续运行 | “下次要这样写”变成可撤销、可追踪的记录 |

一句话：**聪明的部分负责写和提议；有权的部分必须可检查；最终生效必须经过人。**

---

## 📚 每周它替你记住四件事

| 问题 | BriefLoop 记录什么 | 常见位置 |
|---|---|---|
| 这次简报做到哪了？ | 当前阶段、缺失文件、阻塞原因、下一步动作 | `/briefloop status`、`workflow_state.json`、`agent_handoff.md` |
| 每个数字哪来的？ | Claim Ledger、来源日期、来源附录、质量门禁结果 | `claim_ledger.json`、`source_appendix.md`、`quality_gate_report.json` |
| 它学到了什么？ | 只有人工批准的读者偏好；未批准建议不会生效 | `improvement/ledger.jsonl`、`improvement_memory_snapshot.md` |
| 什么在替你把关？ | 阶段完成记录、reader-final gate、交付检查 | `finalize_report.json`、`state finalize-complete` |

它会观察、会提议，但只有你批准的东西才会被记住，而且会记在你能打开、能审计、能撤销的账上。

---

## 📦 你最终会拿到什么？

一次正常运行后，真正给读者看的交付稿通常是：

- `output/delivery/brief.md`
- `output/delivery/<report-name>.docx`

同时，系统会保留一套审计材料，例如：

- `output/intermediate/claim_ledger.json`：关键事实和来源登记；
- `output/source_appendix.md`：来源附录；
- `output/intermediate/quality_gate_report.json`：质量门禁结果；
- `event_log.jsonl`：运行过程记录；
- `improvement/ledger.jsonl`：人工批准的反馈和读者偏好。

这些审计材料不是为了让普通读者阅读，而是为了在被追问、复盘、交接、排错时有据可查。

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
```

创建第一份简报工作区：

```bash
multi-agent-brief onboard
multi-agent-brief init ~/mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ~/mabw-workspace
```

### Windows PowerShell

Windows 不需要 WSL 或 Git Bash。推荐直接用 PowerShell。

```powershell
winget install Python.Python.3.12

git clone https://github.com/Stahl-G/briefloop.git
cd briefloop

.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1

multi-agent-brief version
```

如果 PowerShell 拦截脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

---

## 🤖 Claude Code 路径

如果你使用 Claude Code，可以安装 writer 入口：

```bash
source .venv/bin/activate
multi-agent-brief claude install --repo-workdir .
```

然后使用五个主要命令：

```text
/briefloop new
/briefloop run <workspace>
/briefloop status <workspace>
/briefloop feedback <workspace> [text-or-file]
/briefloop deliver <workspace>
```

`/mabw` 仍然保留为兼容 alias。

建议新用户先看：

- [黄金路径](docs/golden-path.zh-CN.md)
- [我每周怎么用 BriefLoop](docs/weekly-use.zh-CN.md)
- [Claude Code quickstart](docs/claude-code-quickstart.md)

---

## 🧪 三条上手路径

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

当前版本：**v0.10.1**

当前主要入口：

- CLI：`multi-agent-brief`
- shell alias：`briefloop`
- Claude command：`/briefloop`
- 兼容 alias：`/mabw`

v0.10.1 加入了实验性的 Product OS / ReportPack 方向，例如：

- `ReportSpec`
- `ReportPack`
- `ReportTemplate`
- `PolicyProfile`
- workspace skeleton
- delivery / audit bundle manifest
- 实验性的 `evidence_extract` source/scope 注册入口
- 实验性的 SourceHub Lite 来源设置入口，用于本地文本文件、RSS feed 和
  runtime web-search handoff task
- 实验性的内部 review release-mode approval record

这些功能的定位是：**让报告类型、默认策略和交付包更产品化**。

但它们仍然只是元数据、默认配置、设置入口、审批记录和投影控制，不代表系统已经能自动解析 PDF、执行隐藏搜索或爬取、判断行业合规、投资建议、披露可用性、语义真实性，或授权公开发布。

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
| 读者偏好 | Reader Preference | 人工批准的写作要求，例如“先讲影响再讲背景” |
| 改进账本 | Improvement Ledger | 保存已批准反馈的记录 |
| 司乐师 | Orchestrator | 调度各角色、维持流程边界的运行时角色 |
| 交付包 | Delivery Bundle | 给读者看的 Markdown / Word 文件 |
| 审计材料 | Audit Artifacts | 给复盘、排错、追问用的中间记录 |

---

## 🗂️ 文档入口

- [功能地图](docs/features.zh-CN.md)
- [黄金路径](docs/golden-path.zh-CN.md)
- [我每周怎么用 BriefLoop](docs/weekly-use.zh-CN.md)
- [架构状态](docs/architecture-status.zh-CN.md)
- [路线图](docs/roadmap.zh-CN.md)
- [红线与反模式](docs/red-lines-and-anti-patterns.md)
- [公开运行摘要](docs/reference-runs/v0.7.2-public-solar-integration.zh-CN.md)
- [失败研究](docs/reference-runs/v0.7.4-organoid-failure-study.zh-CN.md)

---

## 🤝 合作

这个项目最需要的不是更多概念，而是真实场景。

欢迎这些人参与：

- 每周真实写行业周报、市场简报、IR 材料、管理层材料的人；
- 想用真实工作流试点 BriefLoop 的团队；
- 研究 agent evaluation、human-in-the-loop、可审计 AI 工作流的人；
- 愿意从 issue、文档、测试、示例场景开始贡献的人。

可以从 [good first issue](https://github.com/Stahl-G/briefloop/issues) 开始。提交前建议先读 [红线与反模式](docs/red-lines-and-anti-patterns.md)。

---

## 📄 License

MIT
