# Multi-Agent-Brief-Workflow

<p align="center">
  <a href="README_en.md">English</a> |
  <a href="README.md">简体中文</a>
</p>

一个基于来源、可审计、可由 AI agent 协作执行的简报工作流，用于生成商业、研究、市场、政策、公司跟踪和管理层汇报材料。

> 让代码负责整理流程，让模型负责判断表达，让每一个重要结论都可以追溯来源。

`multi-agent-brief-workflow` 不是一个简单的"AI 写周报"Prompt。它尝试把真实工作中的 briefing 流程拆成更可靠的步骤：理解用户需求、发现来源、整理材料、建立事实账本、辅助写作、审计风险、输出文档。

本项目适合这些场景：

* 行业周报、市场观察、政策简报
* 公司跟踪、竞争对手动态、财报季观察
* 投资者关系、战略研究、管理层汇报
* 研究员、实习生、管培生、战略/投研/IR/总裁办日常简报工作
* 想把 AI agent 用到真实 research workflow 或 office work 的开发者

本项目不是投资建议工具，不是交易信号生成器，也不能替代人工审核。

---

## 为什么做这个项目

在企业战略部、券商研究所、基金投研、投资者关系、总裁办、管理层办公室等场景中，很多人都会花大量时间制作日报、周报、月报、晨会材料和领导层简报。

这些工作重要，但流程高度重复：

* 找新闻、公告、财报、RSS、网页和本地资料；
* 判断哪些信息真正值得写进本期简报；
* 去掉重复、过时、低质量内容；
* 把零散事实整理成结构化分析；
* 核对数字、日期、来源和事实依据；
* 检查 AI 是否写出了没有来源支撑的判断；
* 修改措辞、压缩篇幅、调整结构；
* 最后输出成 Markdown、Word 或其他协作格式。

这个项目希望把这类 briefing 工作抽象成一个开源 workflow，让人把时间更多花在判断、提问和决策支持上，而不是重复搬运和排版。

---

## 它解决什么问题

很多 AI 生成报告的问题不是"写得不够快"，而是：

* 不知道一句话的来源在哪里；
* 数字和日期容易丢失出处；
* 多轮修改后引用关系断掉；
* 来源太多，重复、过期、低质量信息混在一起；
* Prompt 一长，模型容易跳步骤；
* 最终文档看起来完整，但无法审计；
* 个人或团队想开源工具时，容易把真实公司、邮箱、路径或敏感信息写进文档。

本项目围绕这些问题设计：

```mermaid
flowchart LR
  A["用户需求"] --> B["来源发现"]
  B --> C["来源整理"]
  C --> D["事实账本"]
  D --> E["Agent 辅助写作"]
  E --> F["审计校验"]
  F --> G["Markdown / Word 输出"]
```

详细架构见 [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md)。

---

## 功能概览

### 1. 独立简报工作区

项目可以为每个简报任务创建独立 workspace，用来保存用户需求、来源配置、输入材料和输出结果，避免把公司材料、个人任务和工具源码混在一起。

典型工作区包含：

* `config.yaml`：运行配置
* `sources.yaml`：来源策略和来源配置
* `user.md`：用户画像、任务目标和关注重点
* `input/`：本地输入材料
* `output/`：最终输出产物

### 2. 用户画像和任务目标

项目会生成 `user.md`，记录这份简报的背景：

* 关注哪个公司或组织；
* 所属行业或主题；
* 读者是谁；
* 简报频率是什么；
* 重点看什么；
* 哪些来源不能用；
* 输出风格偏好是什么。

这样 agent 在工作前可以先理解"这份简报为什么做、给谁看、什么重要、什么不能写"。

### 3. 来源发现和来源管理

项目支持手动指定来源，也支持让 agent 根据 `user.md` 规划候选来源。

可处理的来源类型包括：

* 本地 Markdown / TXT / JSON 文件
* 手动添加的 URL
* RSS / Atom 订阅源
* Tavily Web Search
* 新闻、公告、filings 等 API 扩展
* MCP / CLI / cached source package 等扩展来源

来源不会直接变成最终结论，而是先被整理、筛选、去重和确认。

### 4. 输入分类与来源治理

项目在 `input/` 下预设四个子目录，并提供了 `multi-agent-brief inputs classify` 命令，自动将输入文件按角色分类：

| 目录 | 角色 | 路由目标 | 是否进入 Claim Ledger |
|------|------|----------|----------------------|
| `input/sources/` | 📄 证据文件 | Scout → Claim Ledger | ✅ 是 |
| `input/feedback/` | ✏️ 编辑反馈 | Editor | ❌ 否 |
| `input/instructions/` | 📋 任务要求 | Analyst / Editor | ❌ 否 |
| `input/context/` | 📎 背景参考 | Analyst | ❌ 否 |

`input/` 根目录下的文件向后兼容，视同证据。

这解决了核心问题：用户的修改意见、任务说明、背景材料**不会被误当作事实来源**写入简报。Scout 子智能体被约束只从证据目录提取声明，非证据文件由 orchestrator 路由给对应的子智能体作为指导。

### 5. 事实账本 Claim Ledger

Claim Ledger 是项目的核心设计之一。它会记录重要事实、来源、证据文本、链接、时间和编号。

最终简报中的重要表述应当可以追溯到 Claim Ledger，而不是由模型凭空生成。

这适合解决：

* AI 编造事实；
* 数字不知道来源；
* 领导追问出处时无法回溯；
* 多轮编辑后引用丢失；
* 需要保留审计记录的研究工作。

### 6. Agent 辅助写作

项目为 Claude Code、Codex ，opencode和其他 AI agent 提供结构化工作环境。Agent 可以基于用户画像、来源材料和事实账本完成：

* 信息整理；
* 重点筛选；
* 草稿生成；
* 管理层口径改写；
* 中英文简报生成；
* 结构优化；
* 风险提示；
* 编辑和审计。

项目不鼓励直接把"最终结论"完全交给单个 Prompt，而是把来源、事实、审计和输出拆开，降低模型顺手编造的空间。

### 7. 审计和质量检查

项目内置审计工具，用于检查简报中的常见风险：

* 引用了不存在的事实编号；
* 数字缺少来源；
* 来源过期；
* 事实账本缺少证据；
* 重复事实；
* 潜在敏感信息泄露；
* 投资建议或交易信号式表达；
* 占位符、内部流程残留或低质量文本。

### 8. Markdown 和 Word 输出

项目支持输出 Markdown，也可以生成 Word 文档。

输出产物包括：

* `brief.md`：给人阅读的 Markdown，不包含内部 `[src:CLAIM_ID]` 标记
* `intermediate/audited_brief.md`：带 `[src:CLAIM_ID]` 的审计版本
* `intermediate/claim_ledger.json`
* `intermediate/audit_report.json`
* `intermediate/source_map.md`
* `brief.docx`，如果启用 DOCX 输出

Word 输出支持标题、表格、列表、引用块、代码块、中英文混排和正式页脚，适合进一步编辑和内部流转。

最终交付文件会同时保留稳定入口和自动命名副本。例如 `brief.md` 旁边会生成按配置命名的 Markdown；如果启用 DOCX，也会生成同名 `.docx`。默认模板是：

```yaml
output:
  filename_template: "{project_name}_{report_date}"
  named_outputs: true
```

### 9. Hermes / Claude Code / Codex 多运行时适配

- **Hermes（主路径）**：`multi-agent-brief hermes install-plugin` 一键安装插件。在 Hermes 中用 `/mabw new` 开始新建简报，`/mabw run <workspace>` 运行已有工作区，`/mabw continue <workspace>` 恢复之前的管线。底层走 `delegate_task` 子代理管线（scout → screener → claim-ledger → analyst → editor → auditor），配合 cron 调度。
- **Claude Code**：`/generate-brief <workspace>` 命令。
- **Codex / OpenCode**：`.codex/`、`.opencode/` 目录下提供 agent 配置。

### 10. 开源发布安全检查

项目包含公开发布前的安全检查工具，用于避免 README、文档、示例和配置中出现真实姓名、公司信息、邮箱、凭证、内部路径或敏感上下文。

这适合个人或团队把内部工具逐步整理成开源项目时使用。

---

## 快速开始

### Hermes（主路径）

```bash
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
bash scripts/setup.sh
source .venv/bin/activate

multi-agent-brief hermes install-plugin
hermes plugins enable mabw
```

然后在 Hermes 中输入：

```text
/mabw new
```

Hermes 会先检查环境，再引导你填写简报需求，然后自动创建工作区并走完 scout → screener → claim-ledger → analyst → editor → auditor 子代理管线。详细流程见 [HERMES.md](HERMES.md)。

> **finalize 交付门禁：** 子代理生成 `audited_brief.md` 后运行：
> ```bash
> multi-agent-brief finalize --config <workspace>/config.yaml
> ```
> 去掉内部 `[src:CLAIM_ID]` 标记 → 生成 `brief.md` / `brief.docx` → 验证输出。

### 其他运行时

也支持 Claude Code（`/generate-brief <workspace>`）、Codex、OpenCode。Clone 仓库并 `source .venv/bin/activate` 后：

```bash
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ../mabw-workspace --runtime claude
```

---

## 使用自己的材料

在 Hermes 中运行 `/mabw new`，按对话引导填写简报需求即可。Hermes 会自动创建工作区并走完完整管线。

产物位于 `<workspace>/output/` 目录：`brief.md`、`brief.docx`、`claim_ledger.json`、`audit_report.json`、`source_map.md`。

如果使用其他运行时，也可以手动操作：

```bash
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ../mabw-workspace --runtime claude
```

---

## 可选：启用 Web 搜索

Web 搜索支持多个后端，默认使用 Tavily。

| 后端 | 环境变量 | 适用场景 |
|------|----------|----------|
| Tavily | `TAVILY_API_KEY` | 默认，快速公开搜索 |
| Exa | `EXA_API_KEY` | 深度研究、论文、财报 |
| Brave | `BRAVE_SEARCH_API_KEY` | 独立索引、通用搜索 |
| Firecrawl | `FIRECRAWL_API_KEY` | 全文提取 |
| Serper | `SERPER_API_KEY` | Google 垂直搜索 |

设置环境变量：

```bash
export TAVILY_API_KEY=<your-key>
```

PowerShell:

```powershell
$env:TAVILY_API_KEY = Read-Host "Enter your Tavily API key"
```

初始化时可在交互问答中选择启用 Tavily：

```bash
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
```

详细配置和后端对比见 [docs/search-backends.md](docs/search-backends.md)。

注意事项：

* API key 必须放在环境变量中，不要写进 README、配置文件或聊天记录；
* Web 搜索结果可能缺少可靠发布时间；
* 时间敏感简报仍应人工核实来源；
* 不同后端在日期质量、证据质量上有差异。

---

## 可选：启用飞书集成

通过官方 [lark-cli](https://github.com/larksuite/cli) 实现双向飞书集成——既可以从飞书文档、会议、表格、日程、审批采集数据，也可以把生成的简报发送到飞书。

### 安装 + 配置

```bash
npx @larksuite/cli@latest install      # 安装（仅首次）
lark-cli config init                    # 配置应用凭证
lark-cli auth login --recommend         # 登录授权
lark-cli auth status                    # 验证是否成功
```

### 从飞书采集数据（输入）

在工作区 `sources.yaml` 中添加飞书数据源：

```yaml
feishu:
  enabled: true
  sources:
    - name: "周例会纪要"
      token: "V1Mdjflk..."       # 飞书文档/妙记 URL 中的 token
      type: minutes               # 可选类型见下表
```

**支持的数据源类型：**

| 类型 | 说明 | 获取方式 |
|------|------|---------|
| `doc` | 飞书文档 | 打开文档，URL 中的 `.../doc/V1Mdjflk...` |
| `minutes` | 会议妙记（含 AI 摘要/待办） | 打开妙记，URL 中的 `.../minutes/V1Mdjflk...` |
| `base` | 多维表格 | 打开 Base，URL 中的 `.../base/V1Mdjflk...`。还需在 config 填 `table_id` |
| `sheet` | 电子表格 | 打开表格，URL 中的 `.../sheet/V1Mdjflk...` |
| `agenda` | 今日日程 | 无需 token |
| `approval` | 审批任务 | 无需 token |

采集的数据会自动进入来源收集流程，与其他来源（manual、RSS、web search）一起被处理。

### 把简报发到飞书（输出）

**发送到聊天群：**

```python
from multi_agent_brief.delivery.feishu import FeishuDeliveryConnector
from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryTarget

connector = FeishuDeliveryConnector()
connector.deliver(
    DeliveryArtifact(path="output/brief.md", title="每日简报"),
    DeliveryTarget(channel="chat", recipient="oc_your_chat_id"),
)
```

`chat_id` 从飞书群聊 URL 中的 `.../?chat_id=oc_xxxxxxxxxxx` 获取，或者在群聊信息中查看。

**创建飞书文档：**

```python
connector.deliver(
    DeliveryArtifact(path="output/brief.md", title="周报"),
    DeliveryTarget(channel="doc"),
)
```

**上传文件到云空间：**

```python
connector.deliver(
    DeliveryArtifact(path="output/brief.docx", title="周报"),
    DeliveryTarget(channel="drive"),
)
```

### 典型工作流

```bash
# 1. 采集需求并初始化工作区
multi-agent-brief onboard
multi-agent-brief init my-workspace --from-onboarding onboarding.json

# 2. 检查配置
multi-agent-brief doctor --config my-workspace/config.yaml

# 3. 将 workspace 交给 agent runtime 生成简报
#    multi-agent-brief run --workspace my-workspace

# 4. 简报生成完毕后，发送到飞书群
python -c "
from multi_agent_brief.delivery.feishu import FeishuDeliveryConnector
from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryTarget
FeishuDeliveryConnector().deliver(
    DeliveryArtifact(path='my-workspace/output/brief.md', title='周报'),
    DeliveryTarget(channel='chat', recipient='oc_your_chat_id'),
)
"
```

详细说明见 [docs/feishu-integration.md](docs/feishu-integration.md)。

---

## 可选：启用 SEC Filing 解析（disclosure-filing-resolver）

通过 [disclosure-filing-resolver](https://github.com/Stahl-G/disclosure-filing-resolver) 集成 SEC EDGAR 公开披露文件自动获取和 XBRL 财务数据提取。适用于跟踪美国上市公司（中概股、外资股或美国本土公司）的季度报告、年度报告和重大事件披露。

### 它能做什么

| 能力 | 说明 |
|------|------|
| SEC 文件获取 | 自动下载 10-K、10-Q、8-K、6-K 等 SEC 文件的 HTML 原文 |
| 6-K 展开 | 自动识别 6-K 文件并展开附录（Exhibit 99.x），提取财务报表、运营回顾等 |
| XBRL 数据提取 | 从 SEC companyfacts API 提取收入、净利润、资产、EPS 等结构化财务数据 |
| iXBRL 解析 | 从 HTML 文件中的 Inline XBRL 标签提取财务事实 |
| 来源可追溯 | 每条财务数据都带 SEC 原文链接，可直接写入 Claim Ledger |

### 安装

```bash
pip install disclosure-filing-resolver
```

### 配置

在工作区的 `sources.yaml` 中添加 `filing_resolver` 配置：

```yaml
filing_resolver:
  enabled: true
  tickers:
    - AAPL      # 替换为你实际跟踪的公司 ticker
    - MSFT
  filing_types:
    - 10-K      # 年报
    - 10-Q      # 季报
    - 8-K       # 重大事件
  xbrl: true    # 启用 XBRL 财务数据提取
```

### 通过来源发现自动配置

如果你使用 `llm_decide` 来源模式，运行 `sources decide` 时会自动生成 SEC filing 候选来源：

```bash
# 1. 生成候选来源（会包含 SEC EDGAR filing 建议）
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml

# 2. 查看候选来源
cat ../mabw-workspace/source_candidates.yaml
# filing_sources 部分会列出建议的 SEC filing 来源

# 3. 编辑 source_candidates.yaml，修改 ticker 为你实际的公司代码

# 4. 合并到 sources.yaml
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge
```

合并后，`sources.yaml` 会自动：
- 添加 `filing_resolver` 到 `enabled_providers`
- 配置 `filing_resolver` 的 tickers 和 filing_types

### 设置 SEC User-Agent

SEC EDGAR 要求声明 User-Agent：

```bash
export SEC_USER_AGENT="your_email@example.com disclosure-filing-resolver"
```

### 典型工作流

```bash
# 1. 安装 disclosure-filing-resolver
pip install disclosure-filing-resolver

# 2. 设置环境变量
export SEC_USER_AGENT="your_email@example.com disclosure-filing-resolver"

# 3. 采集需求并初始化工作区
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json

# 4. 发现来源（自动生成 SEC filing 候选）
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml

# 5. 编辑 source_candidates.yaml，确认 ticker
# 6. 合并
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge

# 7. 将 workspace 交给 agent runtime 生成简报
# multi-agent-brief run --workspace ../mabw-workspace
```

简报中会自动包含来自 SEC 文件的财务数据，例如：

```markdown
- ACME Corp reported revenue of $150.0M for Q1 2026, up 12% year-over-year. [src:FILING_ACME_10Q]
```

详细说明见 [disclosure-filing-resolver 文档](https://github.com/Stahl-G/disclosure-filing-resolver)。

---

## 输出示例

审计版 Markdown 中的重要表述会带有来源引用：

```markdown
## Market

- Synthetic module price checks showed a 3.5% week-over-week decline in selected spot-market channels. [src:MARKETDA_867A7D67D0]
```

给人阅读的 `brief.md` 会去掉内部引用标记；回溯关系保留在 `intermediate/audited_brief.md`、`claim_ledger.json` 和 `source_map.md`。

对应事实会写入 `claim_ledger.json`：

```json
{
  "claim_id": "MARKETDA_867A7D67D0",
  "statement": "Synthetic module price checks showed a 3.5% week-over-week decline in selected spot-market channels.",
  "source_id": "MARKET_DATA",
  "evidence_text": "Synthetic module price checks showed a 3.5% week-over-week decline in selected spot-market channels."
}
```

审计结果会写入 `audit_report.json`：

```json
{
  "audit_status": "pass",
  "audit_score": 100,
  "findings": []
}
```

---

## 文档导航

* [架构设计](docs/architecture.zh-CN.md)
* [当前架构状态](docs/architecture-status.zh-CN.md)
* [迁移说明](docs/MIGRATION.zh-CN.md)
* [Orchestrator Contract 模型](docs/orchestrator-contracts.zh-CN.md)
* [Orchestrator 架构](docs/orchestrator-architecture.zh-CN.md)
* [Claude Code 工作流](docs/claude-code-workflow.md)
* [Claude Code 快速开始](docs/claude-code-quickstart.md)
* [Agent 协作设计](docs/agent-collaboration.md)
* [审计与质量门控](docs/harness.md)
* [Windows PowerShell 支持](docs/windows-powershell.md)
* [飞书集成](docs/feishu-integration.md)
* [路线图](docs/roadmap.zh-CN.md)
* [v2.0 MAS Runtime 重构评估](docs/mas-v2-evaluation.zh-CN.md)

---

## 路线图

项目当前路线图从“继续扩张功能”切换为“先冻结 v1.0 可信参考实现，再探索 v2.0 MAS Runtime”。

下一阶段公开方向：

* v0.6：Orchestrator contracts and feedback loop，让 main agent 明确负责协调 subagents、验证 artifacts，并尽早展示“产出 -> 反馈 -> 有界修复”的闭环。
* v0.7：FrictionStore and improvement proposals，把 recurring failures、audit findings 和 human feedback 转成受控改进建议。
* v0.8：Policy packs and runtime parity，支持不同简报场景，同时保持多 runtime 的一致 artifact 期望。
* v0.9：Distribution and reference workflows，降低安装、配置和 public-safe demo 的门槛。
* v1.0：Stable orchestrated brief workflow，冻结本地优先、可审计、contract-governed 的稳定基线。

### v2.0：MAS Runtime 候选方向

v2.0 不作为短期主路径。v1.0 冻结后，再探索 Shared World、Event Store、TaskBoard、AgentMessage、ClaimProposal / ClaimReducer、run replay 和最小协调协议。

公开路线图见 [docs/roadmap.zh-CN.md](docs/roadmap.zh-CN.md)，v0.6 控制模型见 [docs/orchestrator-architecture.zh-CN.md](docs/orchestrator-architecture.zh-CN.md)，v2.0 技术评估见 [docs/mas-v2-evaluation.zh-CN.md](docs/mas-v2-evaluation.zh-CN.md)。v0.6.3 在共享 Orchestrator authority、minimum runtime state、artifact registry status、decision event 和 feedback/repair control plane 之上，增加 deterministic material-fact、freshness 和 target-relevance gates；这些 gates 可以按当前 stage 阻断 unsafe continue/finalize，并把 repair 归属留给 Orchestrator 显式处理。它不表示 Python 会自动改稿、执行 repair、live-fetch market data、recrawl sources、做 semantic truth judgment 或实现 provenance graph。详细实现规划、schema 草案、私有评测样例和商业场景设计不会放进公开仓库，直到对应能力稳定并适合发布。

---

## 当前状态

这是项目的初始公开发布版本，仍处于早期阶段。当前重点是验证：

* 真实 briefing 工作流是否可以被拆成可复用模块；
* Claim Ledger 是否能降低 AI 编造和来源丢失问题；
* Claude Code / Codex 等 agent 是否能在明确边界下稳定协作；
* 不同行业、岗位和报告类型需要哪些模板和来源策略。

欢迎试用、提 issue、提交 PR，或提供真实工作场景中的痛点和反例。

---

## 欢迎参与

尤其欢迎以下背景的朋友参与：

* 企业战略部、总裁办、管理层办公室；
* 券商研究所、基金投研、PE/VC、产业投资；
* 投资者关系、董秘办、公司治理、法务合规；
* 行业研究、市场研究、政策研究；
* 咨询、产业研究、竞争情报、商业分析；
* 经常制作日报、周报、月报、晨会材料、领导简报的实习生、管培生或初级分析师；
* 正在尝试把 AI agent 用到真实 office work、research workflow 或 internal briefing 的开发者。

你可以参与：

* 提出一个真实使用场景；
* 反馈最痛苦、最重复的 briefing 环节；
* 提供某个行业的周报结构建议；
* 设计行业模块、岗位模块或外部分析模块；
* 试用 demo 并指出哪里不像真实工作流；
* 提交 issue、discussion 或 pull request；
* 帮助完善中英文文档、示例、测试和安全边界。

---

## 项目承诺与隐私安全边界

`multi-agent-brief-workflow` 的目标不是把用户数据交给一个不可见的后台黑箱，而是提供一个本地优先、来源可追溯、过程可审计的 open-source briefing workflow。

### 我们承诺

* **免费与开源**：本项目核心代码以MIT开源许可证发布。用户可以自由查看、运行、修改和二次开发已有开源版本。

* **本地优先**：默认工作流围绕本地 workspace 运行，用户输入材料、配置文件、中间产物和输出文件保存在用户本地目录中。

* **无项目方后台黑箱**：本项目本身不依赖项目维护者控制的 SaaS 后台，也不要求用户把材料上传到项目方服务器。

* **可审计设计**：项目通过 Claim Ledger、source map、audit report 和来源引用机制，尽量让简报中的重要事实、数字、日期和判断可以回溯到具体来源。

* **安全意识**：项目鼓励用户不要把 API key、公司内部资料、真实客户信息、邮箱、内部路径或其他敏感内容提交到公开仓库、README、Issue、PR 或聊天记录中。

### 需要用户理解的边界

* **可审计不等于自动正确**：Claim Ledger 和审计工具可以帮助发现缺失来源、重复事实、过期来源、敏感信息和高风险表达，但不能保证所有事实天然正确。正式分发前仍需要人工审核。

* **本地优先不等于永远离线**：如果用户主动启用 Tavily、OpenAI、Anthropic、Google、MCP、新闻 API、网页搜索或其他第三方服务，相关查询内容、来源 URL 或提示词可能会发送给对应服务商。请在使用前阅读并理解第三方服务的隐私政策和数据处理规则。

* **用户材料由用户自己负责**：请不要把未授权的公司机密、客户数据、个人隐私、受监管信息或其他敏感材料输入到不可信的模型、API 或公开环境中。

* **本项目不是投资建议工具**：生成内容仅用于研究、整理、写作辅助和内部简报草稿，不构成投资建议、交易信号、法律意见或合规结论。

* **安全不是绝对承诺**：本项目会尽力提供清晰的流程、可读的代码和安全检查工具，但任何软件都不能承诺不存在漏洞或误用风险。欢迎用户通过 Issue 或 Pull Request 报告问题和改进建议。

简而言之：

本项目希望让 AI briefing workflow 更透明、更可控、更容易审计，而不是把研究工作变成一个无法解释的黑箱。

---

## 安全与非投资建议声明

不要提交凭证、token、webhook、原始内部日志、私有报告、客户名称、机密文件、内部路径或公司特定 prompt。

本仓库中的示例应使用公开数据或合成数据。

本项目可以帮助组织研究和简报流程，但不提供法律、金融、投资、交易、税务或合规建议。任何真实分发或决策使用前，都需要人工审核。

---

## 版本

完整的版本历史和变更说明请参见 [CHANGELOG.md](CHANGELOG.md)。

当前版本：**v0.6.3** — deterministic material-fact, freshness, and target-relevance gate controls

v0.6.3 增加 `multi-agent-brief gates check/show/validate`，生成 `output/intermediate/quality_gate_report.json`，用于 deterministic material-fact、freshness 和 target-relevance gate。Gate finding 会区分当前阻断 stage 和 repair 归属 stage；`state check` / `state decide` 会阻止带有 blocking gate finding 的当前 stage 继续。Hermes 主路径会在 `finalize` 前显式运行 gates/state；`finalize` 本身不是 quality-gate executor，也不会自动改稿、执行 repair、live-fetch market data、recrawl sources 或自动创建 feedback issue。

[查看完整变更日志 →](CHANGELOG.md)

---

## 开发

运行测试：

```bash
python -m pytest -q
```

生成或检查 agent 配置：

```bash
python scripts/generate_agent_configs.py --check
```

## 许可证

## 交互问答配置项

初始化向导会询问以下 13 个问题（含条件追问）：

1. **公司名称** - 简报目标公司或组织
2. **岗位角色** - 总裁办/战略、投关、行研、政策研究、管理层支持、其他
3. **所属行业** - 制造业、银行、基金、互联网、通用研究
4. **简报标题** - 自定义简报名称
5. **阅读对象** - 管理层、战略团队、研究团队、投资者关系、市场团队等
6. **关注领域** - 逗号分隔，如：销量数据、智驾技术、政策法规、供应链
7. **简报频率** - 每周、双周、每月、不定期
8. **每期条目数** - 默认 8 条
9. **历史检索 / RAG** - 是否启用（默认否）。启用后追问检索引擎：Ollama 本地 / Gemini API
10. **输出格式** - 逗号分隔，如：markdown, docx
11. **来源时效** - 最大来源天数，默认 14 天
12. **来源策略** - 保守（仅官方）、研究（平衡）、激进信号、自定义、LLM 自动决定
13. **实时搜索** - 是否启用（默认否）。启用后追问搜索后端：tavily / exa / brave / firecrawl / serper
