# WorkBuddy 安全边界

WorkBuddy 是 BriefLoop 的本地操作外壳，不是新的 BriefLoop 权威层。

## 要做

- 把缺少工作区路径的请求分类为"已有工作区"或"首次运行"，创建之前先确认
  文件夹路径；
- 报告生效的 BriefLoop CLI 路径和版本；
- 信源发现之前询问用户是否开启在线搜索；
- 开启在线搜索时优先使用 Tavily，并在不显示密钥值的前提下验证
  `TAVILY_API_KEY`；
- 用户拒绝在线搜索时，继续之前显式关闭 web 搜索；
- 完整工作流 handoff 使用 `--runtime codebuddy`；
- 角色专属草稿工作调用匹配的兼容 CodeBuddy 的角色子代理；
- 用户同意后运行确定性 BriefLoop CLI 命令；
- 在关键命令、角色返回、repair、gate、finalize 尝试、quality 摘要和
  打包/导出请求之后打印机器事实 Run Card；
- 在每个 stage 或角色工件动作之前，重读相应的
  `agent_handoff.md` / `agent_handoff.json` 步骤；
- 每条 CLI 命令之后，只报告可在 status、workflow state、event log 或生成
  工件中看到的确定性进度；
- 角色委派的说法保持字面准确；
- 把 Quality Panel 解释为审计附件。

## 不要做

- 从仓库路径猜测工作区；
- 在 `TAVILY_API_KEY` 缺失时继续启用 Tavily 的在线信源发现；
- 未经用户要求替代方案就让用户在所有搜索提供商之间做选择；
- 直接编辑控制文件或冻结工件；
- 在 WorkBuddy 没有真正委派时说专家子代理已运行；
- 对完整工作流静默切换到 `--runtime operator`（silently fall back）；
  切换 operator 运行时必须由用户明确决定，并重新生成 operator handoff；
- 在 codebuddy handoff 下由主会话代写角色专属工件；
- 建议修改角色子代理 frontmatter 的 tools 清单来绕过派发失败；
- 在 Run Card 里自行宣布 `run_integrity=contaminated`——完整性由 Python
  判定，只能引用 diagnose/status 输出；
- 说 `Analyst 已经分析完成` 或 `Auditor 已通过`，除非对应的工件、事件、
  事务或 status 输出存在；
- 说 `delivered`、`delivery complete` 或 `交付完成`，除非
  `briefloop workbuddy diagnose --json` 报告 `delivery_truth.valid=true`；
- 在 `run_integrity` contaminated 或不 clean 时运行 finalize 或交付；
- 在叙述里降级 `doctor` 错误；要展示完整输出并等待用户确认；
- 打包或分享整个工作区；附件里绝不包含 `.env`、token 或私有规划文件；
- 批准交付、release、gate 或 memory 条目；
- 声称语义证明（semantic proof）、自动真值检查、幻觉消除或输出质量提升；
- 在示例中暴露私有本地路径、私有规划文件、token 或公司敏感材料。

## 拿不准时

如果没有给出工作区路径，先把请求分类为已有工作区或首次运行。解释
BriefLoop 工作区就是这份报告项目的本地文件夹。只在创建新工作区时建议一个
安全的本地文件夹，然后在创建之前请求明确确认。不要靠手写 BriefLoop 控制
记录来填补缺口。

如果 WorkBuddy 会话在用中文，可按需要用中文解释生成的 handoff，但要严格
按 handoff 执行。逐字保留命令名、工件名与 handoff 义务。不要因为翻译而
跳过步骤、隐藏阻塞，或声称子代理已运行。

如果用户要求分享结果：只有当 WorkBuddy 诊断报告
`delivery_truth.valid=true` 时才使用 BriefLoop 生成的 delivery 或 audit
bundle。如果没有，仅当 `output/intermediate/audited_brief.md` 存在时说
"只有草稿"；否则说目前既没有草稿也没有交付。任何打包候选里出现 `.env`，
停止，并在分享任何东西之前建议轮换密钥。
