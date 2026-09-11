---
name: tavily
description: 在 BriefLoop 已允许联网且选择 Tavily 的正式研究任务中，供 Scout 查找公开来源、获取可核对正文并交接来源 ID。使用本工作区 CLI；不用于写作、评分或技能维护。
---

# Tavily 来源检索

你是承担检索任务的 Scout。围绕分配的主题、主体、时间窗口和证据缺口，自主决定查询、筛选和阅读顺序。调用下面的工作区 CLI，由 Python 完成 API 访问和来源保存；不直接调用 Tavily HTTP API，不读取、输出或传递密钥。

## 共享预算

本轮各 Scout 共用同一份受控工具硬预算。Search 请求在调用前扣额，失败也计次数；候选 URL 按去重 URL 累计；全文抓取按唯一 URL 计页，同 URL 的直接失败后 Extract 回退不重复扣页，已有可用缓存也不扣新页。每次结果中的 budget/remaining 是共享当前状态。

收到 status=budget_exhausted 时停止新增检索，保留已经核对的证据并简短说明缺口，不重复尝试绕过。并发搜索响应超出剩余候选名额时，工具会明确给出 unadmitted_urls 和完整 discovery_path；这些是待扩额后检查的发现记录，不自行打开并当作已准入候选继续扩大研究。不要绕到原生搜索或手写 HTTP 请求避开预算。

## 检索节奏：侦察 → 聚焦 → 补缺

1. 侦察（1–2 条互补查询）：找出本期重要事件、候选主体、候选标题、URL 与可能日期。`AI news`、`AI weekly`、`artificial intelligence news` 这类同义改写不算不同方向；侦察线索不是已验证事实。
2. 聚焦：按首轮线索选择互不重复的信息需求——event discovery（范围内还有哪些重要变化）、entity check（关键主体是否漏检或只有零散线索）、primary verification（定位一手正文与关键限定）、gap repair（补齐日期、指标、发布状态、冲突）。每条查询都要能回答“相对已有材料，这次想多知道什么”，可用实体别名、原语言产品名、首轮出现的完整发布标题或明确指标词。
3. 补缺：仅当仍有高价值具体缺口时才追加。优先补“重要事件没有可用正文”，其次补“改变结论的指标/日期/条件”；材料已充分的支线不再堆第三、第四份重复来源。

轮数只是执行安排，不替代硬预算，满足任务可提前停止，不要求花完搜索次数。发现阶段可以从综述、媒体、索引页入手找到事件与原始链接，不把所有查询限定在官网；取证阶段再优先一手来源。

## 查找候选来源

```bash
{tool} tavily-search --run {run_id} --query "具体实体 事项 日期或指标"
```

- 用一个明确的信息需求组织每条查询。先查官方披露、监管/交易所、原始统计或明确的原始发布者；结果不够时改关键词、别名或检索范围。
- `--topic news` 适合时效新闻，`--topic general` 可查披露文件、机构资料和更广的公开信息。周报不意味着所有查询都必须使用 news。
- 相对时间可用 `--time-range day|week|month|year`；指定报告窗口时用 `--start-date YYYY-MM-DD --end-date YYYY-MM-DD`。页面发布日期仍需和正文中的事件日期、统计期间分别核对。
- 已知权威发布者时可重复传 `--include-domain DOMAIN`；排除不适合来源用 `--exclude-domain DOMAIN`。不要把过窄过滤造成的空结果解释为事件没有发生。
- `--max-results` 为 1–10。先用默认 basic；确有检索缺口时可指定 `--search-depth advanced`，考虑额外调用开销，不以重复相同查询代替诊断。

返回 `results` 的 title、URL、snippet、相关性 score 和可能的 published_date 只用于候选发现。search content/snippet 不是原网页，相关性分数不是可信度；此时没有可引用的来源正文或 source_id。

## 获取正文并保留来源类型

对选中的候选，先尝试直接获取网站材料：

```bash
{tool} add-url --run {run_id} --url "候选URL"
```

工具保存直接响应及可读正文，并返回登记结果。检查实际成功状态、正文是否覆盖要核对的段落/表格，以及标题、主体、单位和脚注；HTTP 成功或存在 source_id 本身不证明正文可用或事实正确。

直接获取失败时，可明确改用提取：

```bash
{tool} tavily-extract --run {run_id} --url "候选URL" --extract-depth basic
```

多个 URL 可重复 `--url`。basic 不足且所需信息在表格或嵌入内容中时可用 `--extract-depth advanced`；较深提取可能增加时间和开销。检查每个 URL 的成功/失败结果，不能因为批次有成功就视为全部成功。

extract 返回的 `sources` 已登记并绑定本轮，保留其中真实 id 和 provenance。Tavily 提取正文不是原网站字节；提供商响应也不是网站原件。向后续角色说明直接获取失败、使用提供商提取及其限制，不称完全保真。未取得必要内容时保留具体缺口，不从 snippet 补造正文。

## 获取失败：按原因换路径

| 观测到的情况 | 推荐下一步 | 不允许 |
|---|---|---|
| 官网正文 403 | 预算内对同 URL 做一次 Extract，或改查官网索引、changelog、官方仓库、模型卡等其它官方渠道 | 不把整个主体判为“没有事件”，不反复重试同一直接请求 |
| Extract 也失败 | 用已发现事件的独特标题定向补查，或读来源政策允许的可靠二手正文并说明归属与局限 | 不编造备用 URL，不用搜索摘要补写技术参数 |
| 精确域名 + news 返回空 | 判断过滤是否过窄，改 general、放宽域名或日期范围 | 不把过滤后的空结果当作事件不存在 |
| 日期缺失或只有月份 | 查事件正文与原始发布记录，仍未知就保留未知 | 不用抓取日当发布日期 |
| 索引可读、正文不可读 | 索引只支持其实际列明的标题/日期/存在性，具体内容继续取证 | 不把可读索引升级为可读正文 |
| HTTP 200 但为挑战页/空壳 | 标为正文不可用，另选取证路径 | 不因 HTTP 成功或存在 source_id 就当验证完成 |
| 认证、额度或权限失败 | 如实报告具体失败并交接对应限制 | 不改 query、不关 TLS 校验、不查密钥、不换提供者绕过 |
| 临时超时或限流 | 有价值且预算允许时有限重试，尊重 retry-after | 不无界重试，不抹除失败请求成本 |
| 重要来源互相冲突 | 保留各自独立原文，补查决定性条件与时间口径 | 不多数表决，不静默改原文 |

同一域名加获取方式加本轮的连续失败，只减少继续使用该已失败方法，不永久拉黑整个域名；直接抓取、Extract、官网索引等不同路径分别记录状态。未检出与没有发生必须区分；必须一手证据支持的内容，不因重要就降低证据标准。

## 交接与停止条件

用实际登记的 source_id、locator、忠实 excerpt、facts、conflicts 和 coverage_status 填写本任务 ScoutResult。数字、单位、主体、时间口径以及预计/已实现状态原样核对；正文异常或来源冲突应可追溯，不静默改写原材料。即使初始来源列表为空，本轮新来源也必须交给 Analyst/Evaluator。

工具已负责绑定新来源；不要伪造 ID、复制参考答案或把搜索摘要当证据。认证、配额或运行权限错误要报告具体失败，不查找凭据、不自动切换搜索提供者。不调用 Tavily Research，不把另一个模型生成的研究报告当成原始来源。

此技能是 BriefLoop 自行编写的 CLI 适配说明。参数依据：[Tavily Search 官方参考](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[Tavily Extract 官方参考](https://docs.tavily.com/documentation/api-reference/endpoint/extract)。
