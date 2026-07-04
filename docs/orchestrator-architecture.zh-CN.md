# 司乐师架构（Orchestrator Architecture）

本页用公开安全方式说明 v0.6 司乐师架构（Orchestrator Architecture）。

## 核心模型

司乐师（Orchestrator）是 runtime main agent。它可以是 Hermes parent agent、Claude Code command context、Codex main agent、OpenCode primary agent，或 operator fallback。legacy `manual` runtime 值只保留为 `operator` 的 CLI 兼容别名。

Python 保持工具层定位，负责 workspace setup、source handling、deterministic checks、validation helpers、audit support 和 final rendering。Python 不是标准完整 brief-generation runtime。

```text
runtime main agent
  -> reads workspace context
  -> reads frozen audience profile snapshot
  -> reads contract references
  -> identifies the next stage
  -> delegates a specialist role
  -> checks the expected artifact
  -> decides continue / retry / repair / review / block / finalize
```

## 契约引用（Contract References）

v0.6.0 引入公开安全的契约引用（contract references）：

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

这些文件描述共享 authority、decision vocabulary、stage order、产物预期（artifact expectations）和 default policy shell。v0.6.1 增加最小 runtime state control files 和 artifact status checks。v0.6.2 增加最小 feedback issue 和 repair-plan 控制面。v0.6.3 增加 deterministic material-fact、freshness 和 target-relevance gate controls。v0.6.4 增加 packaged public-safe evaluation cases，用于开发和 CI 回归验证。v0.6.5 增加可选 deterministic 溯源投影（provenance projection），用于 workspace audit/debug review。v0.6.6 增加 workspace-local 读者画像（audience profile）和 frozen per-run 读者快照（audience snapshot），并通过运行交接单（handoff）暴露。v0.6.7 增加控制台（Orchestrator control switchboard），用于 deterministic control recommendations 和 enable/defer/reject selection 记录。v0.6.8 增加 finalize 阶段生成的 reader-facing source appendix，只使用 audited brief 实际引用的事实账本（Claim Ledger）来源。v0.6.9 稳定 install/runtime asset parity，并增加 workspace-local OpenCode/Claude Code runtime kits。v0.7.0 增加由人类治理的 Improvement Ledger 和 frozen per-run Improvement Memory snapshot，用于已批准的读者偏好 guidance。Python 仍不自动改 brief 产物、不执行 repair、不 live-fetch sources、不做 semantic truth judgment、不用 LLM judge 给文章打分、不把溯源或 source appendix 当成语义证明，也不自动学习 taste、自动执行 selected controls，或把 FeedbackIssue 直接变成 guidance。

## 四类契约（Contract）

| 类别 | 目的 |
|---|---|
| 行为契约（Behavior） | 定义司乐师和 specialist role 边界。 |
| 流程/产物契约（Process / Artifact） | 定义 stage readiness 和 expected artifact categories。 |
| 事实支撑/证据契约（Fact-Grounding / Evidence） | 保持 material statements 可追溯到 supported claims。 |
| 质量/读者契约（Quality / Audience） | 让 delivery decisions 匹配 reader context。 |

## Decision Vocabulary

司乐师使用统一 decision vocabulary：

- `continue`
- `retry_stage`
- `delegate_repair`
- `request_human_review`
- `block_run`
- `finalize`

在 v0.6.1 中，这些 decision 也可以通过 runtime state event log 记录。v0.6.2 也会记录 feedback issue 和 repair-plan events。v0.6.3 也会记录质量门禁（quality gate）check/pass/block events。v0.6.5 也会记录溯源（provenance）build/validate 结果。v0.6.6 也可以记录读者快照（audience snapshot）creation。v0.6.7 也可以记录控制台 build 和 control selection events。event log 是 control trace；`provenance_graph.json` 是独立的派生投影（projection）。

## Runtime Loop

每个 runtime 都应表达同一套 loop：

1. 读取 `config.yaml`、`sources.yaml`、`user.md`、inputs、交接产物、runtime state files、`output/intermediate/audience_profile_snapshot.md` 和 `output/intermediate/orchestrator_control_switchboard.json`。
2. 从 frozen 读者快照总结和本 run 相关的 taste guidance，供 delegated roles 使用。
3. 当控制台推荐 control 时，记录 enable/defer/reject selection；selection 不等于 execution。
4. 从交接单读取契约引用。
5. 判断当前 stage 和 expected artifact。
6. 将 stage 委派给对应 specialist role 或 Python tool，并在有用时传入 taste summary。
7. 检查 expected artifact 是否存在，并是否适合进入下一 stage。
8. 如果存在 audit findings 或 human feedback，先结构化 issue 和 repair plan，但不执行 repair。
9. 决定 continue、retry、delegate repair、request human review、block 或 finalize。
10. 仅在 audit readiness 后 finalize，并按配置生成 reader-facing outputs 和 `output/source_appendix.md`。

不同 runtime 的机制可以不同，但产物预期不应分叉。

## Runtime Asset Availability

Package install 包含 Python CLI、packaged contract configs、policy packs 和
packaged public-safe eval fixtures。`.agents/`、`.claude/`、`.codex/`、
`.opencode/` 和 `integrations/hermes-plugin/` 等 runtime source directories
属于 source-clone-only。

在源码仓库中运行
`multi-agent-brief runtime install --workspace <workspace> --runtime opencode|claude|codex|all`
可以把 workspace-local OpenCode/Claude Code/Codex commands 或 custom agents 以及
skills 复制到业务 workspace。安装后的 workspace kit 允许 runtime 在业务 workspace 内运行，
而不必读取 MABW source repo。

## Reader-Facing Source Appendix

`multi-agent-brief finalize` 会把 reader delivery bundle 写入 `output/delivery/`。配置 `source_appendix`，或旧配置请求 legacy `source_map` output format 时，来源附录会追加到交付 Markdown/DOCX 末尾，并同时保留 `output/source_appendix.md` 作为 audit/control copy。

- Appendix 只来自 `output/intermediate/audited_brief.md` 实际引用的 claims。
- Reader-facing output 不应暴露 raw `claim_id`、`source_id`、evidence text、本地路径或 `file://` URL。
- Appendix 是交付包里的面向读者来源列表，同时 `output/source_appendix.md` 是 audit/control copy；它不是 runtime state file、产物契约、质量门禁、溯源图，也不是 claim 语义为真的证明。
- 显式 `source_appendix` 请求在事实账本缺失或格式错误时会失败；legacy `source_map` 请求仅作为兼容 alias，可带 warning 跳过。

## 读者画像运行时层面（Audience Profile Runtime Surface）

v0.6.6 会在 init 时创建 workspace-local `audience_profile.md`，并为当前 active run 冻结为 `output/intermediate/audience_profile_snapshot.md`。snapshot 会通过 `agent_handoff.json` 的 `audience_memory_files` 暴露。

- 司乐师在 run 中读取 snapshot，不读取 live profile 作为当前 run 行为依据。
- 中途修改 `audience_profile.md` 只影响下一次 run。
- 读者记忆是 runtime context，不是 source evidence、产物契约、质量门禁、溯源图 node 或 stage blocker。
- Python 负责创建、冻结、暴露和记录这层 context；不自动学习 taste、不自动更新 profile、不 enforce taste，也不基于 taste 做 workflow routing。

## 控制台（Orchestrator Control Switchboard）

v0.6.7 在运行交接单时创建 `output/intermediate/orchestrator_control_switchboard.json`。只有显式运行 `multi-agent-brief controls select` 时，才会写入 `output/intermediate/control_selections.json`。

- 控制台把 available controls、deterministic recommendations、司乐师 selections 和 execution 分开。
- selection 不等于 execution：选择 `enable` 只记录意图，不会自动运行质量门禁、feedback planning、溯源投影、source discovery、repair 或 subagents。
- 隐私敏感 control 必须有显式 human approval，才会进入 execution-ready 状态。
- 控制台是 runtime control context，不是事实账本 input、final-reader artifact 或 finalize gate。

## 溯源投影（Provenance Projection）

v0.6.5 可以基于已有 runtime state、artifact registry、event log、事实账本、feedback、repair 和质量门禁 files 生成 `output/intermediate/provenance_graph.json`。这个溯源图是 audit/debug projection：

- 保留 artifact identity、producer stage or role、consumer stage or role 和 validation summaries 作为 graph metadata。
- 只由 `multi-agent-brief provenance build` 创建。
- 不初始化 runtime state，也不执行 workflow stages。
- 记录 citation 和 control relationships，不做语义证明。
- 默认不阻断 `state check`、`state decide` 或 `finalize`。

v0.6.5 不实现 semantic proof、source support graph、execution replay 或 full DAG runtime。

## Deferred Work

后续 v0.6 milestone 负责：

- private/commercial benchmark suites
- LLM-as-judge prose scoring
- semantic evidence support verification
- execution replay 或完整 DAG runtime

## Related

- [司乐契约模型（Orchestrator Contract）](orchestrator-contracts.zh-CN.md)
- [当前架构状态](architecture-status.zh-CN.md)
- [迁移说明](MIGRATION.zh-CN.md)
- [v0.6.0 Explicit Orchestrator Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)
