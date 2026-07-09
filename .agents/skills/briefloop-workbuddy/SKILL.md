---
name: briefloop-workbuddy
description: Operate BriefLoop from WorkBuddy through CodeBuddy-compatible role subagents and deterministic CLI transactions. Use for requests like "跑周报", "生成行业简报", "运行简报", "briefloop", "industry weekly", or "market brief".
---

# BriefLoop WorkBuddy Skill

## Scope

当 WorkBuddy 用户想要创建、打开、查看、运行、修复或总结一个 BriefLoop
工作区时，使用本 Skill。当用户用自然语言提出"跑周报"、"生成行业简报"、
"运行简报"、"帮我做市场简报"这类请求、并期望由 BriefLoop 驱动简报工作流时，
也使用本 Skill。

本 Skill 是围绕 BriefLoop CLI、CodeBuddy 项目角色代理和工作区工件的
面向 WorkBuddy 的适配层。它不是新的 BriefLoop 权威层：它不证明语义真实性、
不批准交付、不自行执行 gate，也不在 WorkBuddy/CodeBuddy 没有真正委派时
声称角色子代理已运行。本源码包只能从 BriefLoop 源码检出获得；在打包命令
补齐之前，Python wheel/sdist 包安装不包含这些 WorkBuddy 文件。

## Purpose

帮助 WorkBuddy 用户通过确认过的本地工作区、兼容 CodeBuddy 的角色子代理、
`briefloop` CLI 事务和生成的 handoff 工件来操作 BriefLoop，而不把
WorkBuddy 的对话文字变成运行时权威。

## Use When

当 WorkBuddy 会话中的请求涉及周报、行业简报、市场简报、文档审阅、已有
BriefLoop 工作区的查看、修复、状态、质量摘要或交付准备时使用。如果用户只是
在改 BriefLoop 源码，请改用仓库开发技能。

## Inputs

- 用户的报告主题，或已有工作区路径。
- 当前生效的 `briefloop` 命令路径和版本。
- 已存在的工作区文件、status 输出和生成的 handoff 工件。
- 创建新工作区或执行交付前，用户的明确确认。

## Outputs

- 用户可以自行核查的 BriefLoop CLI 命令。
- 基于 status、workflow state、event log 或生成工件的确定性进度摘要。
- 仅在 handoff 指派时产出的角色子代理草稿工件。
- 绝不直接编辑 Python 拥有的控制文件或冻结工件。

## 首检（First Checks）

操作工作区之前：

1. 定位当前生效的 BriefLoop 命令：

   ```bash
   BRIEFLOOP_CLI="$(command -v briefloop)"
   test -n "$BRIEFLOOP_CLI"
   "$BRIEFLOOP_CLI" version
   ```

2. 向用户报告解析出的二进制路径和版本。
3. 如果 `briefloop` 不可用，请用户先激活源码检出的虚拟环境或完成安装。
4. 正确使用首跑搜索默认值：
   - 询问用户是否要为这个工作区开启在线搜索；
   - 如果开启在线搜索，强烈推荐 Tavily，并先检查 `TAVILY_API_KEY`；
   - 如果启用了 Tavily 搜索但 `TAVILY_API_KEY` 缺失，说明需要先配置密钥
     才能进行在线信源发现；
   - 如果用户拒绝在线搜索，在继续之前显式关闭 web 搜索；
   - 只有当用户主动问起其他提供商时才提及 Exa、Brave、Firecrawl 或 Serper；
   - 绝不打印 API key 的值；只报告对应环境变量是否存在。
5. 如果用户没有给出工作区路径，不要只问"工作区在哪里？"，先分类：
   - 已有工作区：请用户给出文件夹路径；
   - 首次运行：主动提出创建一个。
6. 向用户解释：BriefLoop 工作区就是这份报告项目的本地文件夹。创建之前，
   请用户对目标路径做出明确确认。
7. 创建工作区时使用产品入口，并把用户的搜索选择写明：

   ```bash
   # 用户开启在线搜索；强烈推荐 Tavily
   briefloop new industry-weekly <workspace> --search-backend tavily
   briefloop new management-monthly <workspace> --search-backend tavily
   briefloop new document-review <workspace> --search-backend tavily
   briefloop new solar-periodic <workspace> --search-backend tavily

   # 用户拒绝在线搜索
   briefloop new industry-weekly <workspace> --web-search-mode disabled
   briefloop new management-monthly <workspace> --web-search-mode disabled
   briefloop new document-review <workspace> --web-search-mode disabled
   briefloop new solar-periodic <workspace> --web-search-mode disabled
   ```

`solar-periodic` 是实验性产品入口，使用前要先说明。

## 搜索默认值

BriefLoop 推荐用 Tavily 做在线搜索，但生成的工作区在用户明确开启之前，
在线搜索保持 `configure_later`。首次运行时询问用户：

```text
是否要打开在线搜索？如果要打开搜索，强烈建议添加 Tavily API。
```

如果用户开启在线搜索，先配置 Tavily：


```bash
TAVILY_API_KEY=<user-provided-key>
```

然后只验证 `TAVILY_API_KEY` 是否存在，不要显示密钥内容。除非用户明确要求
替代方案，不要让用户在 Tavily、Exa、Brave、Firecrawl、Serper 之间做选择。

如果用户拒绝在线搜索，用 `--web-search-mode disabled` 或
`web_search.enabled: false` 显式关闭。

## 运行模式

完整的 BriefLoop 工作区通过 CodeBuddy 运行时执行：

```bash
briefloop run --workspace <workspace> --runtime codebuddy
```

只有当源码检出包含 `.codebuddy/skills/briefloop/SKILL.md` 和
`.codebuddy/agents/briefloop-*.md` 时才使用 `--runtime codebuddy`。
仅有本地 WorkBuddy Skill zip 不会安装这些 CodeBuddy 项目资产。

WorkBuddy 主会话拥有确定性 CLI 事务。角色专属的草稿工件工作必须调用匹配的
兼容 CodeBuddy 的角色子代理，然后回到主会话执行验证、gate、state、
finalize、交付和 quality 命令。

当 handoff 指派对应阶段时，严格使用这些角色名：

- `briefloop-scout`
- `briefloop-screener`
- `briefloop-claim-ledger`
- `briefloop-analyst`
- `briefloop-editor`
- `briefloop-auditor`
- `briefloop-formatter`

签入的角色定义位于：

```text
.codebuddy/agents/briefloop-*.md
```

如果当前 WorkBuddy 环境无法调用这些角色子代理（例如 Agent 工具无法按
frontmatter 受限工具集派发项目级子代理），在 codebuddy 完整工作流执行之前
停下。你仍可以运行确定性 setup、`status`、`state check`、`quality
summarize` 或 `demo` 命令，但在 codebuddy handoff 下不要退回手写 BriefLoop
JSON 工件，不要静默切换到 `--runtime operator`，也不要建议修改角色子代理
frontmatter 的 tools 清单来绕过设计。

此时合法的继续通道只有一条，且必须由用户明确决定：向用户说明本环境无法
派发角色子代理，请用户选择是否改用 operator 运行时重新生成 handoff：

```bash
briefloop run --workspace <workspace> --runtime operator
```

operator handoff 是主机无关的紧凑工作流，明确允许主会话亲自起草角色工件
（operator-authored artifact work），不假设也绝不声称子代理运行过。用户
同意后按新生成的 operator handoff 逐步执行；用户不同意则停在当前状态并
输出 Run Card。

在每个 stage 或角色工件动作之前、以及每条 BriefLoop CLI 命令之后，先重新
打开 `output/intermediate/agent_handoff.md` 和
`output/intermediate/agent_handoff.json` 中相应的步骤再继续。不要跳过
handoff 步骤；除非 WorkBuddy/CodeBuddy 确实委派并记录了那个角色，不要声称
某个子代理已经运行。

每个确定性 CLI 事务之后，向用户总结进度。只报告可在 `status`、
`workflow_state.json`、`event_log.jsonl` 或生成工件中看到的完成状态。

## Run Card 协议

在每个关键 CLI 命令、角色返回、repair 动作、gate 检查、finalize 尝试、
quality 摘要或打包/导出请求之后，打印一张机器事实 Run Card。不要用自由
发挥的"已完成"总结代替 Run Card。

严格使用这些字段，未知值填 `unknown` 而不是猜测：

```text
runtime:
current_stage:
run_integrity:
blocked:
latest_gate_status:
finalize_report:
delivery_truth:
next_allowed_action:
```

这些值从 `briefloop workbuddy diagnose --workspace <workspace>
--json` 读取；该命令格式化的是规范 completion projection，只对
`next_allowed_action` 叠加 WorkBuddy 的 doctor/密钥安全覆盖。不要从
`workflow_state.json`、`event_log.jsonl` 或文件存在性检查重构交付、gate、
finalize 或下一步动作的真值。如果 `delivery_truth.valid` 不是 `true`，
Run Card 不得声称已交付。仅当角色专属草稿工件（例如
`output/intermediate/audited_brief.md`）确实存在时才说 run 里有草稿；
否则说目前既没有草稿也没有交付。

`run_integrity` 等完整性判定字段同样只能引用 diagnose/status 的输出；
完整性由 Python 判定，不要根据自己的操作推断或自行宣布 contamination。

## 硬停规则

只在会让所请求动作不安全的条件下立即停止，并只展示机器证据。不要把
finalize 之前的正常状态当作流程停止。

1. `briefloop doctor` 报告任何错误。展示完整 doctor 输出、实际工作区路径、
   当前用户、输出路径存在性/可写性检查结果、以及平台权限/ACL 输出。不要在
   叙述里降级该错误；除非用户明确确认证据，不要把 doctor 标记为已完成。
2. 对 finalize、交付、导出或分享请求：如果 `run_integrity` 不是 clean，或
   处于 `contaminated`、`stale_or_invalid`、unknown 状态，停止该动作。
   不要运行 finalize 或交付。下一步安全动作是全新 run、受控 repair 或人工
   审阅。对于早期阶段的草稿工作，报告 Run Card，并只继续 handoff 允许的
   非交付工作流步骤。
3. 对交付、导出、分享或完成声明：如果 WorkBuddy 诊断载荷没有报告
   `delivery_truth.valid=true`，停止该动作。不要说 "delivered"、
   "交付完成"或 "delivery complete"。
   仅当 `output/intermediate/audited_brief.md` 存在时才说有草稿；否则说
   目前既没有草稿也没有交付。
   只有 handoff 和 Run Card 允许时才继续更早的角色工作阶段。
4. 任何导出、分享、打包、zip 或附件候选包含 `.env`、token、私有规划文件
   或机器密钥。停止，告诉用户移除该包，并建议轮换任何暴露的密钥。绝不分享
   整个工作区 zip。

## Work

对请求分类，确认或创建工作区路径，运行确定性 BriefLoop 命令，只为 handoff
指派的草稿工作调用角色子代理，在 stage 或工件动作前重读当前 handoff，在
gate、status、角色代理可用性或用户意图不明确时停下。细节使用 reference
文件，不要在这个入口文件里扩张权威。

## Handoff

把 `output/intermediate/agent_handoff.md` 和
`output/intermediate/agent_handoff.json` 当作这个工作区的执行契约。在每个
stage 或角色工件动作之前、以及每个确定性 CLI 事务之后，先重读相应的
handoff 步骤再继续。

## Required References

行动之前读相应的 reference：

- `references/quickstart.md`
- `references/workspace-workflow.md`
- `references/artifact-boundary.md`
- `references/status-and-gates.md`
- `references/repair-protocol.md`
- `references/workbuddy-safety.md`

## 硬边界

- 不得直接编辑 `workflow_state.json`、`artifact_registry.json`、
  `runtime_manifest.json`、`event_log.jsonl`、gate 报告、release 报告或
  冻结工件。
- 不要用 WorkBuddy 的对话文字代替 BriefLoop 事务。
- 除非用户明确要求且当前 gate 允许，不要执行交付。
- 不要批准 release、人工审批账本或 memory 条目。
- 不要把可追溯性说成语义证明（semantic proof）或输出质量提升。
- 不要说 "Analyst is complete" 或 "Auditor passed"，除非对应的工件、事件、
  status 或事务确实存在。
- 除非 `briefloop workbuddy diagnose --json` 报告
  `delivery_truth.valid=true`，否则不要说"已交付"。
- 不要打包或分享整个工作区。存在时使用 BriefLoop 生成的 delivery 或 audit
  bundle；绝不包含 `.env`。如需支持排查，只分享经人工确认的非敏感摘录，
  来自 `briefloop status --json` 或 doctor 输出。
- 工作区路径、生效二进制、gate 状态或交付意图不明确时，停下来询问。
