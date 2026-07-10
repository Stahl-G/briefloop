# 快速上手

本快速上手面向在本地操作 BriefLoop 的 WorkBuddy 用户。本版本的 Skill 包为
source-clone-only；Python wheel/sdist 包安装不包含这些 WorkBuddy 文件。

## 1. 确认生效的 CLI

运行：

```bash
BRIEFLOOP_CLI="$(command -v briefloop || command -v multi-agent-brief)"
test -n "$BRIEFLOOP_CLI"
"$BRIEFLOOP_CLI" version
```

改动任何东西之前，先报告解析出的命令路径和版本。如果两个命令都不存在，
停下来，请用户安装 BriefLoop 或打开源码检出。

## 2. 询问在线搜索

首次信源发现之前询问用户：

```text
是否要打开在线搜索？如果要打开搜索，强烈建议添加 Tavily API。
```

BriefLoop 推荐用 Tavily 做在线搜索，但生成的工作区在用户明确开启之前保持
`configure_later`。如果用户开启在线搜索，用 Tavily 作为推荐提供商：

```text
TAVILY_API_KEY=<user-provided-key>
```

只检查 `TAVILY_API_KEY` 是否存在，不要打印密钥内容。除非用户要求非 Tavily
提供商，不要让用户在 Exa、Brave、Firecrawl、Serper 之间做选择。

如果用户拒绝在线搜索，创建工作区时显式关闭 web 搜索。

## 3. 创建工作区

如果用户说"跑周报"而且还没有工作区：

1. 用一句话解释：BriefLoop 工作区就是这份报告项目的本地文件夹。
2. 建议一个位于 BriefLoop 源码检出之外的安全本地文件夹，例如 macOS/Linux 的
   `~/Documents/BriefLoop/workspaces/<topic-slug>` 或 Windows 的
   `C:\Users\<User>\Documents\BriefLoop\workspaces\<topic-slug>`。
3. 创建之前请用户对目标路径做出明确确认。只建议；不要静默创建文件夹或
   工作区。
4. 根据用户的自然语言请求选择产品入口：
   - weekly、industry、market、competitor、周报、行业、竞品 ->
     `industry-weekly`
   - management monthly、管理月报、月报 -> `management-monthly`
   - file review、PDF review、document review、文件、PDF、审阅 ->
     `document-review`
5. 用户确认目标路径之后才运行 `briefloop new ...`。

一次只用一个产品入口，并把用户的搜索选择写明。

如果用户开启在线搜索，强烈推荐 Tavily：

```bash
briefloop new industry-weekly <workspace> --search-backend tavily
briefloop new management-monthly <workspace> --search-backend tavily
briefloop new document-review <workspace> --search-backend tavily
briefloop new solar-periodic <workspace> --search-backend tavily
```

如果用户拒绝在线搜索，显式关闭：

```bash
briefloop new industry-weekly <workspace> --web-search-mode disabled
briefloop new management-monthly <workspace> --web-search-mode disabled
briefloop new document-review <workspace> --web-search-mode disabled
briefloop new solar-periodic <workspace> --web-search-mode disabled
```

`industry-weekly`、`management-monthly`、`document-review` 是基线支持的
产品入口。`solar-periodic` 是实验性入口。

## 4. 运行 CodeBuddy Handoff

运行：

```bash
briefloop run --workspace <workspace> --runtime codebuddy
```

然后查看：

```bash
briefloop status --workspace <workspace>
briefloop state check --workspace <workspace>
```

handoff 之后，只报告能在文件或 CLI 输出中看到的确定性进度，例如：

```text
已创建工作区。
已生成 CodeBuddy handoff。
当前状态：等待 source/scout artifact。
```

不要说 `Analyst 已经分析完成` 或 `Auditor 已通过`，除非对应的工件、事件、
事务或 status 输出存在。

每个关键命令或角色返回之后，用机器事实打印这张 Run Card：

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

如果 `doctor` 报告任何错误，停下并展示完整 doctor 输出再继续。如果
`run_integrity` 处于 `contaminated`、`stale_or_invalid` 或 unknown 状态，
停止 finalize、交付、导出与分享动作，不要运行 finalize 或交付。
`run_integrity` 为 `contaminated_repaired` 时，不要再次运行 finalize；仅当
`delivery_truth.valid=true` 时才可交付，并且永久不具备 reference 资格，否则
停止交付。对更早的角色工作阶段，报告 Run Card，并只继续 handoff 允许的
非交付工作流步骤。
如果 WorkBuddy 诊断没有报告 `delivery_truth.valid=true`，不要声称已交付，
也不要导出交付包。仅当 `output/intermediate/audited_brief.md` 存在时才说
run 里有草稿；否则说目前既没有草稿也没有交付。只有 handoff 允许时才继续
更早的阶段。

WorkBuddy 主会话必须为 handoff 指派的草稿工作调用匹配的角色子代理：

```text
briefloop-scout
briefloop-screener
briefloop-claim-ledger
briefloop-analyst
briefloop-editor
briefloop-auditor
briefloop-formatter
```

如果这些角色子代理不可用，在完整工作流执行之前停下。不要退回手写
BriefLoop JSON 工件，也不要静默切换到 `--runtime operator`。

## 5. 生成质量摘要

当工作区已有足够工件可以总结时：

```bash
briefloop quality summarize --workspace <workspace>
```

打开 `output/intermediate/quality_panel.html` 查看静态审计视图。
Quality Panel 是可追溯性和过程问责，不是语义证明、交付批准或 release
授权。

## 6. 安全分享输出

不要打包或分享整个工作区。完整工作区可能包含 `.env`、token、私有规划
笔记、控制文件和未完成工件。存在时使用 BriefLoop 生成的 delivery 或
audit bundle。如果某个打包或附件候选包含 `.env`，停止，移除该包，并建议
轮换任何暴露的密钥。
