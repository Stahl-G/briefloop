# BriefLoop 功能地图

BriefLoop 的功能很多，因为它不是一个单点报告生成器，而是一条简报闭环：输入、证据、事实、写作、审计、交付、反馈和评估。这个页面按“用户能做什么”整理当前功能。

状态说明：

- **默认在场**：属于受支持的问责主链。
- **可选启用**：需要配置来源、runtime 或外部工具。
- **受支持基线**：属于 v0.11 产品基线入口面。
- **实验性**：已实现，但还不是 v0.11.0 稳定契约。
- **路线图**：已规划或已定边界，但不是已实现能力。

当前产品基线：**v0.10.7 release line 上的 v0.11 产品基线目标**。

## 启动和运行一份简报

| 功能 | 做什么 | 状态 | 入口 |
|---|---|---|---|
| 工作区 onboarding | 收集简报目标、读者、节奏、来源模式和输出偏好，再创建 workspace | 默认在场 | `multi-agent-brief onboard`, `multi-agent-brief init --from-onboarding` |
| 产品工作区骨架 | 从受支持的 baseline ReportPack 创建 conservative local-first workspace 和 `report_spec.yaml` | 受支持基线 | `briefloop new industry-weekly <workspace>`, `briefloop new management-monthly <workspace>`, `briefloop new document-review <workspace>` |
| Claude writer 命令 | 给写作者提供五动词入口 | 可选启用，一等 writer 路径 | `/briefloop new`, `/briefloop run`, `/briefloop status`, `/briefloop feedback`, `/briefloop deliver`；`/mabw` 保留为兼容 alias |
| Runtime handoff | 为外部 orchestrator 和 subagents 生成执行交接面 | 默认在场 | `multi-agent-brief run --workspace <workspace>` |
| 状态查看 | 查看当前 stage、blocker、artifact、计时 bucket 和下一步安全动作 | 默认在场 | `/briefloop status`, `multi-agent-brief status` |
| 交付包 | 在 finalize 检查后输出读者可见 Markdown 和 DOCX | 默认在场 | `/briefloop deliver`, `multi-agent-brief finalize`, `state finalize-complete` |

## 来源和输入采集

| 功能 | 做什么 | 状态 | 说明 |
|---|---|---|---|
| 本地手工输入 | 使用 workspace 中已有的本地文件 | 默认在场 | 最适合第一次试用 |
| 缓存 source pack | 复用已经下载好的公开或私有来源包 | 可选启用 | 适合可复跑 run |
| Runtime web search | 让当前 agent runtime 用自己的搜索工具找来源 | 可选启用 | 不需要 BriefLoop API key |
| 外部搜索 API | 使用 Tavily、Exa、Brave、Firecrawl、Serper 等搜索后端 | 可选启用 | 需要 API key |
| RSS / 新闻源 | 跟踪配置好的 RSS 和新闻 API | 可选启用 | 适合周度监控 |
| SEC / filing 工具 | 拉取 filing，并解析 ticker / XBRL 来源 | 可选启用 | 适合公司跟踪和 IR 场景 |
| 飞书 / Lark 来源集成 | 通过本地工具拉取配置好的飞书材料 | 可选启用 | 需要本地集成配置 |
| MinerU 文档解析 | 解析 PDF/DOCX/PPTX/XLSX | 可选启用 | 高级解析需要 token |
| MCP / CLI source provider | 允许 MCP server 或 CLI 脚本贡献来源候选 | 可选启用 | provider 输出会先规范化 |

## 证据和可追溯性

| 功能 | 记录什么 | 状态 | 边界 |
|---|---|---|---|
| Claim Ledger | 写作前登记关键事实、source ID、source date 和 claim metadata | 默认在场 | 只提供可追溯性，本身不等于语义证明 |
| Source Appendix | 给交付稿提供读者安全的来源列表 | 配置后默认在场 | 原始 trace 细节不进入 delivery |
| Artifact Registry | 记录 expected artifacts、hash、producer 和验证状态 | 默认在场 | Python 拥有的控制面 |
| Runtime Manifest | 记录每次 run 的 runtime 状态、hash、snapshot 和兼容 metadata | 默认在场 | 每次 run 冻结 |
| Event Log | 记录 stage transition、gate block、repair、finalize 等事件 | 默认在场 | append-only 事件轨迹 |
| Run Archive | 保存完成 run 的 artifacts 和 summary | 归档后默认在场 | 冻结参考面 |

## 门禁、修复和交付安全

| 功能 | 保护什么 | 状态 | 边界 |
|---|---|---|---|
| Stage-complete transaction | 没有所需 artifacts 和状态记录时不允许 stage 前进 | 默认在场 | CLI transaction，不靠 prompt 记忆 |
| Quality gates | freshness、material fact、target relevance、coverage/omission continuity、editor-new-fact 等 finding | 默认在场 | 确定性 gate 可以阻断 |
| Reader-final gate | 拒绝读者面残留，例如内部 claim ID、流程词、错误 source marker、空 citation row | final delivery 默认在场 | 只扫描 reader-facing surface |
| Run integrity contamination | 显式标记 frozen artifact 被改、replay 风险和完整性违例 | 默认在场 | contaminated run 不是干净 reference evidence |
| Repair routing | 把 blocker 和 finding 分配给有边界的 repair 路径 | 已支持，仍在改进 | repair 不抹掉原始 trace |

## 反馈和已批准记忆

| 功能 | 做什么 | 状态 | 边界 |
|---|---|---|---|
| Feedback capture | 记录用户反馈，用于 review、repair 或 intake | 已支持 | feedback 不自动等于 memory |
| Improvement Ledger | 保存人工批准的读者偏好，append-only、可审计 | 使用时默认在场 | 不是自动学习 |
| Improvement Memory snapshot | 把已批准 guidance 冻结进下一次 run 的 runtime surface | 使用 Improvement Ledger 时默认在场 | 只影响后续 run，不追溯当前 run |
| Supersede / revert hygiene | 防止明显 guidance 腐化，并保留可撤销性 | 已支持 | 人类控制 |

## 实验性支持记录控制面

这些 v0.9.x 控制面是可选实验功能。它们提高 traceability 和 support records 能力，但不会把 BriefLoop 变成真理证明机。

| 功能 | 增加什么 | 状态 | 不代表 |
|---|---|---|---|
| Atomic Claim Graph | Claim Ledger 条目的可选 atom-level 分解 | 实验性 | 自动 atomization 正确 |
| Evidence Span Registry | 可选 source-pack byte binding 和 span trace record | 实验性 | 语义支撑证明 |
| Claim-Support Matrix | 可选 atom-to-evidence support rows，以及 validation 和 gate/status projection | 实验性 | 自动支撑评估、真理证明或 release eligibility |
| Semantic support assessment proposals | 面向 support label 的结构化多评估者 proposal layer | 路线图 | 单个模型 judge 决定真伪 |
| Human adjudication queue | 人类解决有争议的 support assessment | 路线图 | 自动裁决 |
| Release eligibility | 根据 support 和评估记录进行显式 reference/release 分类 | 路线图 | 隐藏质量声明 |

## 评估和 dogfooding

| 功能 | 做什么 | 状态 | 边界 |
|---|---|---|---|
| 确定性测试集 | 在 CI 中运行 1,000+ 个不调用 LLM 的测试 | 默认在场 | 测控制行为和契约，不测模型质量 |
| Synthetic demos | 用安全合成材料展示证据链 | 已支持 | demo 不等于生产质量声明 |
| Reference run reports | 发布 public-safe integration / failure studies | 已支持 | 每份 report 都说明能证明什么、不能证明什么 |
| MABW-080 / BriefLoop-090 experiments | 注册、评分、总结受控实验 run | 实验性 | 初步证据，不是通用质量证明 |

## 输出格式

| 功能 | 输出 | 状态 |
|---|---|---|
| Markdown delivery | `output/delivery/brief.md` | 默认在场 |
| DOCX delivery | `output/delivery/<named>.docx` | 已支持 |
| Source appendix | 配置后进入 delivery 的来源列表，以及 audit copy | 已支持 |
| PDF / advanced rendering | 通过 renderer / tooling 路径输出 | 可选启用 |

## CLI 发现命令

如果你想看机器可读的 feature catalog，而不是这份产品功能地图，可以用：

```bash
multi-agent-brief features
multi-agent-brief features --info <feature-id>
multi-agent-brief features --json
multi-agent-brief recommend --text "Track competitors and SEC filings"
multi-agent-brief setup <workspace>
multi-agent-brief doctor
```

## 不是功能

BriefLoop 当前不提供：

- 无人类交付的自治报告生成；
- 自动长期记忆；
- 自动语义支撑证明；
- 从 Claim-Support Matrix 自动得出 release eligibility；
- 投资建议、交易信号或法律意见；
- 保证每条链接来源都语义支持每个子主张。

一句话：BriefLoop 可以留下可审计轨迹，并阻断已知失败模式；它不证明真理。
