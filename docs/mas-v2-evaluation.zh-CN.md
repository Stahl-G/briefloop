# v2.0 MAS Runtime 重构评估

本文评估基于维护者提供的 v2.0 重构计划，以及论文 *Leveraging the Learning Curve: Reusing Existing Architectural Patterns to Design and Implement MAS* 中关于 Distributed Systems、MAS、ECS、消息通信、任务规划和协调机制的论述。

## 结论

v2.0 方向成立，但启动条件必须后移到 v1.0 Stable Baseline 之后。

这份计划最正确的地方是：它没有把“真正 MAS”理解为更多 agent 名称、更多 Prompt、更多并发或套用某个现成框架，而是把系统控制方式从中央 Pipeline 改为：

```text
Shared World 保存事实与状态
Agent 根据局部视图、目标和能力自主行动
Event / Message 记录协作与因果
Deterministic Systems 负责 Claim、Audit、Rendering 等治理底线
```

这与论文的核心启发一致：MAS 能借用 Distributed Systems 的成熟工程能力，ECS 的价值在于强调数据、实体和行为逻辑分离，而不是要求项目照搬游戏 ECS 框架。

## 对当前系统的判断

当前项目是高质量 multi-role agentic workflow，但还不是严格 MAS：

- `BriefPipeline` 仍控制完整执行顺序。
- Agent 接口更像阶段函数，而不是具备身份、局部状态、目标、收件箱和自治决策的主体。
- Agent 之间主要通过共享内存和顺序读写协作，而不是通过消息、事件和协议协作。
- `ClaimLedger`、Formatter、Renderer、审计规则等更适合作为环境资源或确定性系统，不应被包装成自治 Agent。

这不是缺陷，而是 v1.0 前应保持的优势：顺序 Pipeline 更容易测试、审计、回归和作为未来对照组。

## 论文对 v2.0 的有效支撑

论文中最适合迁移到本项目的不是某个框架，而是四个工程原则：

1. **数据与逻辑分离**
   ECS 把 Entity、Component、System 分开。对应到本项目，BriefRun、Source、ClaimProposal、Claim、AuditFinding、Task 都应成为可持久化实体或组件；Agent 行为通过事件改变世界状态。

2. **环境是协作中心**
   MAS 不是多个对象互相调用，而是多个 agent 在共同环境中感知、行动、协作。对应到本项目，Shared World 应包含 SourceStore、ClaimGraph、TaskBoard、DraftGraph、AuditFindings、AgentStates 和 EventLog。

3. **消息与事件承载通信**
   论文强调 MAS 通信和 DS 消息机制的相通性。对应到本项目，`AgentOutput` 应逐步升级为 typed `AgentMessage` / `Event`，包含 sender、recipient、message type、correlation、causation、entity_id、payload 和 idempotency key。

4. **MAS 最小概念集**
   论文归纳的最小 MAS 概念包括 agent 架构、BDI、message passing、cooperation、task planning、coordination。对应到本项目，v2.0 不需要完整 BDI 框架，但至少需要 BDI-lite：beliefs、goals、intentions、capabilities、constraints、inbox cursor。

## 推荐 v2.0 最小范围

第一阶段只做 `mas-runtime-foundation`，不要重写整个产品。

应做：

- SQLite Event Store。
- `Event`、`AgentMessage`、`Task`、`AgentState`。
- TaskBoard、lease、timeout、reopen。
- 最小 Contract Net / 任务竞标机制。
- `ClaimProposal` 状态机。
- 确定性 `ClaimReducer`。
- Scout 不再直接写 Claim Ledger，而是发布 `ClaimProposed`。
- Screener 不再直接修改 ledger 内部状态，而是发布 `ClaimScored` / `ClaimRejected` / `ClaimSelected`。
- Event replay、agent interruption recovery、并发 proposal 测试。
- 导出与 v1 Claim Ledger 兼容的 artifact。

不应做：

- 不迁移完整 Analyst / Editor / Auditor。
- 不重写 DOCX / Formatter / Delivery。
- 不引入多服务器部署。
- 不引入 Kafka / Redis 作为第一步。
- 不把 MAS Runtime 作为 README 主路径。
- 不让 v2 改变最终报告的产品定义。

## 迁移顺序评估

推荐顺序：

```text
v1.0 baseline
→ Event Store + Shared World
→ ClaimProposal / ClaimReducer
→ Planner + TaskBoard + Scout bidding
→ Analyst-Auditor challenge loop
→ Pipeline becomes legacy adapter
```

这个顺序是合理的，因为 Claim 生成与接受机制是当前 Pipeline 向 MAS 转换的最小本质变化。写作、DOCX 和交付都依赖 Claim 质量，不应最先重构。

## 主要风险

1. **过早重构风险**
   如果 v1.0 的 schema、audit、manifest、golden dataset 没冻结，v2 输出好坏无法判断。

2. **伪 MAS 风险**
   只增加 agent 类、并发 worker 或 LLM 对话，不等于 MAS。必须出现持久化状态、局部视图、typed message、task ownership、coordination protocol 和 replay。

3. **治理弱化风险**
   MAS 不应削弱 Screener、Claim Ledger、Auditor、Final Clean 和 human review。自治只能发生在治理边界内。

4. **复杂度外溢风险**
   Redis、Kafka、多进程、多服务器、完整 RAG、模型路由和全渠道交付都应后置。

5. **Artifact 兼容风险**
   v2 必须继续导出 v1-compatible Claim Ledger、audit report、source map 和 final output，否则无法复用现有审计与渲染生态。

## 验收标准

v2.0 MAS Runtime 第一阶段只有在满足以下条件时才算通过：

- 不存在一个函数写死完整执行顺序。
- Agent 有持久化身份、局部状态和 inbox cursor。
- Agent 能接受、拒绝、转交或创建任务。
- Agent 通过 typed message / event 通信。
- TaskBoard 能处理 lease、timeout 和 retry。
- ClaimProposal 经过确定性 Reducer 才能进入正式 Claim Ledger。
- Event Log 可以 replay。
- v2 输出可与 v1 golden dataset baseline 对比。
- Auditor / release gate 仍可阻止发布。
- Human Review 仍是最终交付边界。

## 建议

将 v2.0 计划保留为 `experimental architecture track`，不要写成短期产品承诺。公开 roadmap 中只应写：

- v1.0 前不启动 MAS 主路径。
- v2.0 第一阶段是 runtime foundation。
- v2.0 必须复用 v1 golden datasets 和 artifact contracts。
- v2.0 的目标是增加自治、通信、协调和可重放性，而不是替代确定性治理系统。
