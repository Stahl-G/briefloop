# 用户简报画像

本文件用于帮助 Codex、Claude Code、OpenCode 或其他 agent 理解用户的简报需求。
它不是新闻来源、不是证据来源，不应被 Scout 当作 source ingestion 输入。

## 基本信息

- 公司：TOYO
- 行业：solar
- 岗位：strategy_office
- 阅读对象：management
- 简报标题：美国光储周报
- 简报频率：weekly
- 最大来源天数：14
- 每期筛选条数：8
- 信息来源策略：research

## 关注领域

- policy
- competitor
- market
- customer_demand

## 来源选择偏好

如果 source_profile = llm_decide，请根据以下原则选择来源：

1. 优先使用公开、可引用、有发布时间的来源。
2. 优先覆盖公司官方、同行公司、监管政策、行业媒体、市场数据、客户需求和竞争动态。
3. 不要使用私有邮件、内部聊天记录、机密报告、客户名称、凭据、token 或重大非公开信息。
4. 对第一次自动发现的来源，应先写入 source_candidates.yaml，等待用户确认后再进入正式 sources.yaml。
5. 所有进入简报的事实仍必须经过 Claim Ledger 和 Auditor。

## Safety

This project is not investment advice, legal advice, tax advice, trading signal generation, or a replacement for human review.
