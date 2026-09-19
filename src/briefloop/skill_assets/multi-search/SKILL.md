---
name: multi-search
description: 使用本轮允许渠道发现来源、保存正文并按具体缺口补查。
---

# 多渠道来源检索

读取下方的本轮冻结策略，优先首选，必要时使用已启用补充渠道。完整调用由 `{tool}` 提供，本轮 ID 是 `{run_id}`。

先用1–2条互补查询发现事件，再按主体、当地语言与一手发布聚焦；补查重要缺口，材料充分即可交接。覆盖对照用于检查单一渠道可能漏掉的地区和观点，不给每条查询机械地同时调用所有渠道。

受控渠道统一用 web-search，可传 --provider、--purpose、--reason、--gap-id、--query、--topic general|news、--time-range day|week|month|year、--start-date、--end-date、--include-domain、--exclude-domain、--max-results、--search-depth basic|advanced。博查为网页搜索，不提供独立news/depth模式；工具返回有效映射。不得读取API密钥或手写网络请求绕过记录。

候选 URL 先用 `{tool} add-url --run {run_id} --url "URL"` 获取原文。Tavily 在允许渠道内时才可对失败页面用 tavily-extract；提取响应不是网站原始字节。HTTP成功、source_id、搜索分数都不是事实证明。摘要和挑战页不算正文。

失败按原因处理：限流尊重retry-after，预算内有限重试；过滤过窄可改关键词/日期/域名；取证失败可查其他公开发布路径。认证或余额错误保留实际状态，已允许替代渠道可继续，不通过换源突破预算或访问权限。公众号、小红书只有链接/摘要时保留具体缺口或请求用户提供材料，不宣称已读全文。

所有Scout共用硬预算；受控search失败也计次，跨渠道同URL与正文复用既有缓存。原生搜索调用数无法可靠观测时为未知，不能加进“全部可控”计数。budget_exhausted时停止受控新增请求并交接，不能绕到其他渠道耗用同一个已耗尽额度。

随读随记证据；若工具提供 record_evidence，以 source_id/source_hash、单一行段 locator、短逐字 quote 交给运行器截取原文，只修正未接纳条目，最后仅提交缺口与检索小结；否则在槽位结果中增量保留逐字 excerpt。交接：真实 source_id、指向所在行/页的 locator、原文逐字的 excerpt、重要事实/冲突、覆盖缺口和实际发现渠道；证据足够就停止，不因剩余额度继续堆转载。不改写原件、不安排递归generate。独立Reviewer只读取已保存材料。
