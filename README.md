# multi-agent-brief-workflow

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

### 4. 事实账本 Claim Ledger

Claim Ledger 是项目的核心设计之一。它会记录重要事实、来源、证据文本、链接、时间和编号。

最终简报中的重要表述应当可以追溯到 Claim Ledger，而不是由模型凭空生成。

这适合解决：

* AI 编造事实；
* 数字不知道来源；
* 领导追问出处时无法回溯；
* 多轮编辑后引用丢失；
* 需要保留审计记录的研究工作。

### 5. Agent 辅助写作

项目为 Claude Code、Codex 和其他 AI agent 提供结构化工作环境。Agent 可以基于用户画像、来源材料和事实账本完成：

* 信息整理；
* 重点筛选；
* 草稿生成；
* 管理层口径改写；
* 中英文简报生成；
* 结构优化；
* 风险提示；
* 编辑和审计。

项目不鼓励直接把"最终结论"完全交给单个 Prompt，而是把来源、事实、审计和输出拆开，降低模型顺手编造的空间。

### 6. 审计和质量检查

项目内置审计工具，用于检查简报中的常见风险：

* 引用了不存在的事实编号；
* 数字缺少来源；
* 来源过期；
* 事实账本缺少证据；
* 重复事实；
* 潜在敏感信息泄露；
* 投资建议或交易信号式表达；
* 占位符、内部流程残留或低质量文本。

### 7. Markdown 和 Word 输出

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

### 8. Claude Code / Codex 工作流适配

项目提供 Claude Code 和 Codex 的 agent 配置，使不同 AI 编程工具可以理解项目角色和边界。

这些配置可以帮助 agent 明确：

* 谁负责找来源；
* 谁负责整理事实；
* 谁负责写作；
* 谁负责编辑；
* 谁负责审计；
* 谁负责格式输出；
* 哪些事情不能做。

### 9. 开源发布安全检查

项目包含公开发布前的安全检查工具，用于避免 README、文档、示例和配置中出现真实姓名、公司信息、邮箱、凭证、内部路径或敏感上下文。

这适合个人或团队把内部工具逐步整理成开源项目时使用。

---

## 快速开始

### 方式一：让 Claude Code 或 Codex 协助运行

打开 Claude Code、Codex 或其他 coding agent，输入：

```text
克隆 https://github.com/Stahl-G/multi-agent-brief-workflow， 并启动交互问答初始化
```

Agent 会读取项目说明，先用自然语言询问简报对象、行业主题、读者、语言、频率和来源偏好，再创建工作区、配置来源、运行示例和生成第一份可审计草稿。

正式分发前仍需要人工确认来源、内容和审计结果。

### 方式二：本地手动运行

macOS / Linux / WSL:

```bash
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
bash scripts/setup.sh
source .venv/bin/activate

multi-agent-brief init ../mabw-workspace
multi-agent-brief run --config ../mabw-workspace/config.yaml
```

PowerShell:

```powershell
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1

multi-agent-brief init ../mabw-workspace
multi-agent-brief run --config ../mabw-workspace\config.yaml
```

运行后查看：

```text
../mabw-workspace/output/brief.md
../mabw-workspace/output/intermediate/audited_brief.md
../mabw-workspace/output/intermediate/claim_ledger.json
../mabw-workspace/output/intermediate/audit_report.json
../mabw-workspace/output/intermediate/source_map.md
../mabw-workspace/output/intermediate/draft_brief.md
```

> 注意：`multi-agent-brief run` 生成的是可审计草稿和中间产物，不等于可直接分发的正式简报。真实工作中建议再经过 Claude Code / Codex 辅助改写、审计和人工确认。

---

## 使用自己的材料

初始化一个工作区。请在交互问答里说明公司/组织、行业或主题、任务目标、读者、语言、频率和来源偏好：

```bash
multi-agent-brief init ../mabw-workspace
```

把 `.md`、`.txt` 或 `.json` 文件放入：

```text
../mabw-workspace/input/
```

检查来源配置：

```bash
multi-agent-brief doctor --config ../mabw-workspace/config.yaml
```

运行工作流：

```bash
multi-agent-brief run --config ../mabw-workspace/config.yaml
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
multi-agent-brief init ../mabw-workspace
```

详细配置和后端对比见 [docs/search-backends.md](docs/search-backends.md)。

注意事项：

* API key 必须放在环境变量中，不要写进 README、配置文件或聊天记录；
* Web 搜索结果可能缺少可靠发布时间；
* 时间敏感简报仍应人工核实来源；
* 不同后端在日期质量、证据质量上有差异。

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
* [Claude Code 工作流](docs/claude-code-workflow.md)
* [Claude Code 快速开始](docs/claude-code-quickstart.md)
* [Agent 协作设计](docs/agent-collaboration.md)
* [审计与质量门控](docs/harness.md)
* [Windows PowerShell 支持](docs/windows-powershell.md)
* [路线图](docs/roadmap.zh-CN.md)

---

## 路线图

### 近期

* 智能语气调节：根据管理层、研究员、IR、法务合规、投资等不同读者自动调整表达方式。
* 自动文档命名：根据公司、主题、日期、频率、语言和报告类型生成输出文件名。
* 多种 DOCX 模板：支持管理层简报、研究笔记、正式内部报告等不同版式。
* 智能市场和竞对策略：根据 `user.md` 自动判断应该跟踪哪些市场、公司、政策和竞争对手。
* 更灵活的搜索策略：支持官方来源、行业媒体、filings、RSS 和 Web Search 的分层配置。

### 中期

* Effort 设置：使用 `low` / `medium` / `high` / `xhigh` 控制搜索深度、模型强度、审计严格度、输出长度和成本。
* 模型路由：为来源规划、信息提取、分析写作、编辑、审计和格式输出配置不同模型。
* RAG 支持：接入历史简报、公司记忆、行业背景和重复事项识别。
* 更多搜索后端：支持更多搜索引擎、新闻搜索 API 和高级搜索策略。
* 可插拔专题模块：支持财报季、竞对跟踪、政策风险、专利/诉讼、市场价格和专题深度报告等模块。

### 长期

* 每日或每周定时采集和草稿生成。
* 飞书、Telegram、邮件、SMS 等发送集成，并加入审计门控。
* PPT 输出和管理层汇报 deck 生成。
* 更完整的企业部署能力，包括私有来源连接器、团队级模板和多模型工作流配置。

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

当前版本：**v0.1.0** — 正式发布版。支持交互式问答初始化、来源发现、事实账本、审计和质量门禁。

最新未发布修复：`multi-agent-brief run <workspace>` 会自动读取该目录下的 `config.yaml`，同时零来源/零 Claim 的空报告会被审计门拦截。

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

公共安全扫描：

```bash
python scripts/public_safe_scan.py
```

---

## 许可证

MIT

## 交互问答配置项

初始化向导会询问以下 10 个问题：

1. **监控内容** - 简报主要监控什么？
   - 默认：公司 + 行业 + 政策 + 竞争对手 + 风险事件

2. **目标受众** - 谁会阅读这份简报？
   - 默认：管理层 / 领导团队

3. **来源广度** - 信息来源的广度如何？
   - 默认：可靠公开来源 + 行业媒体

4. **语言和频率** - 简报的语言和频率？
   - 默认：英文，每周

5. **关注领域** - 最关注哪些具体领域？
   - 示例：销量数据、智驾技术、政策法规、供应链、产品发布

6. **搜索后端** ⭐ - 选择网络搜索提供商
   - tavily (默认，快速AI搜索)
   - exa (深度研究)
   - brave (独立网络索引)
   - firecrawl (搜索+抓取)
   - serper (Google SERP)
   - serpapi (广泛SERP)
   - none (仅本地文件)

7. **每期条目数** - 每份简报包含多少条目？
   - 默认：8 条

8. **来源时效性** - 来源材料的最大年龄（天）？
   - 默认：14 天

9. **审计严格度** - 对来源完整性的要求？
   - standard (默认)
   - strict (严格)
   - lenient (宽松)

10. **禁止来源** - 是否有需要避免的来源或主题？
    - 默认：无
