# MABW 替你记着什么

英文版：`docs/what-mabw-keeps-track-of.md`。

MABW 不只是一个写简报的工具。它更重要的作用，是把一份简报背后的过程记下来。

对写作者和业务用户来说，最有用的心智模型不是一串 control files，而是每周工作循环里的四件事：

```text
本期写到哪了。
每个数字哪来的。
它学到了什么。
什么在替你把关。
```

## 核心承诺

它会观察、会提议；但只有你点头的，才会被记住，而且记在一本你随时能翻、能撤销的账上。

更短地说：

> 系统不会学会任何你没有批准过的东西。

这句话不是说 MABW 从不观察你的工作。它的意思是：观察不等于权力。系统可以建议一个偏好、一个固定格式、一条事实核查，但只有经过批准的 guidance 才能影响未来 run。

用人话说：

> AI 可以写草稿；系统记录账本；只有你能让偏好生效。

## 1. 本期写到哪了

它记什么：

- 当前处在哪个 stage；
- 哪些 stage 已完成、待处理或被阻塞；
- 哪些 artifacts 应该出现；
- 哪些 decisions 已经记录；
- Orchestrator 下一步允许做什么。

你在哪看：

- `output/intermediate/workflow_state.json`
- `output/intermediate/event_log.jsonl`
- `output/intermediate/artifact_registry.json`
- `output/intermediate/runtime_manifest.json`
- `output/intermediate/agent_handoff.md`

出错时它怎么保护你：

> 一份简报不是因为 agent 说“我完成了”就算完成。MABW 能告诉你本期运行到哪了、缺什么、为什么能继续或不能继续。

## 2. 每个数字哪来的

它记什么：

- claim、数字、日期、公司事实、政策、价格、产能、客户和项目进度的来源与支撑；
- 与这些 claim 相关的 audit 和 quality-gate findings；
- 交付包中附在 `brief.md` / DOCX 后的 source appendix 条目。

你在哪看：

- `output/intermediate/claim_ledger.json`
- `output/intermediate/gates/auditor_quality_gate_report.json`
- `output/intermediate/gates/finalize_quality_gate_report.json`
- `output/intermediate/quality_gate_report.json`（latest/legacy projection）
- `output/intermediate/audit_report.json`
- `output/delivery/brief.md`
- `output/delivery/<命名周报>.docx`
- `output/source_appendix.md`
- `output/intermediate/provenance_graph.json`

出错时它怎么保护你：

> 在最终简报里随便指一个数字，问“这个数从哪来的？”系统应该能指回 claim、source、date 和 checks，而不是要求你相信模型记忆。

## 3. 它学到了什么

它记什么：

- 先讲业务影响，再讲背景；
- 不替管理层下决策；
- 使用更简洁的 executive tone；
- 给建议前先说明不确定性。

这些不是事实。它们是针对读者的写作偏好。

你在哪看：

- `improvement/ledger.jsonl`
- `improvement/memory.md`
- `output/intermediate/improvement_memory_snapshot.md`
- `output/intermediate/runtime_manifest.json`

出错时它怎么保护你：

> 如果你希望 MABW 记住一个写作偏好，它必须先经过你批准。未批准建议不会影响未来 run；已批准 guidance 只影响后续 run；被撤销的 guidance 会从后续 snapshot 消失。

## 4. 什么在替你把关

它记什么：

- contracts、gates、policies 和 delivery checks；
- stage 完成前必须存在的 required artifacts；
- final reader-clean 检查，例如 local paths、internal claim IDs 和空 source rows。

你在哪看：

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`
- `output/intermediate/gates/auditor_quality_gate_report.json`
- `output/intermediate/gates/finalize_quality_gate_report.json`
- `output/intermediate/quality_gate_report.json`（latest/legacy projection）
- `output/intermediate/repair_plan.json`

出错时它怎么保护你：

> 有些要求不是偏好，而是交付检查。它们应该在简报定稿前阻断问题，而不是被写成软性的 memory。

## 当反馈被路由到其他地方

用户不应该需要判断一条反馈到底是 taste preference、structure rule、fact correction 还是 delivery gate。MABW 应该把自然语言反馈翻译到正确的 route。

例如用户说：

> 以后每条新闻先讲对公司的影响，再讲背景，不要替管理层下决策。

MABW 可以拆成：

| 用户看到的类型 | 内部 route | 例子 |
|---|---|---|
| 写作偏好 | `memory_guidance` | 先讲对公司的影响。 |
| 固定格式候选 | `checkable_rule_candidate` | 每条 news item 使用 implication -> fact -> uncertainty。 |
| 风格边界 | `memory_guidance` 或未来 checklist | 不替管理层下决策。 |
| 事实或来源核查 | `fact_review` | 修正价格、日期、来源或公司状态。 |
| 已由系统执行 | `already_enforced` | source appendix 已经在交付前检查。 |

任何持久变化发生前，用户都应该先看到系统的理解。

## 建议话术

### 已由系统执行

不要只说“已支持”或“已处理”。要告诉用户机制是什么、在哪里能看到结果。

建议话术：

> 这一点已经是交付标准。每次交付前，系统都会检查来源附录；如果缺失，run 不应该定稿。你可以在交付检查记录里看到结果。

### 事实或来源核查

不要把这说成 memory 拒绝。它是更硬的一条 route。

建议话术：

> 明白。这涉及一个具体事实或来源，比记成写作习惯更重要。我已把它转入本期事实/来源核查。

### 固定格式候选

要说清楚这是升级：可检查规则不应长期停留在 soft memory。

建议话术：

> 明白。这是一条每期都应执行的固定格式。写成偏好不够可靠，我建议在 review 后把它升级为模板或交付规则。

### 写作偏好

要说明 approval 才能影响未来。

建议话术：

> 我可以把它作为未来 run 的写作偏好记住。它不会影响未来输出，除非你确认批准。

## 候选建议应默认可见

如果 MABW 提出一个 preference 或 rule，那这个建议应该默认可见。隐藏建议会削弱信任。

未来 candidate view 可以这样分组：

```text
待你确认的写作偏好
建议加入固定格式
需要核查的事实或来源
系统已在执行
```

用户应该能确认、编辑或忽略这些建议。Candidate parking lot 也必须容易清空；一个堆满旧建议的收件箱，比没有建议更伤信任。

## 批量确认

不是所有确认都有同样风险。

如果用户给出一句反馈，MABW 把它拆成几条，界面可以展示全部拆分项，并允许用户看完后一键提交。

如果系统是从以往通过样本中推断出偏好，那用户并没有亲口说过这些话。这类 machine-proposed preferences 应逐条 review 后再采纳。

经验规则：

```text
用户亲口说过的反馈：可以 grouped review。
机器从样本推断的偏好：必须逐条采纳。
```

## MABW 不应该声称什么

MABW 不应因为存在控制面，就声称输出质量自动提升。

当前控制面可以证明：

- 一条 approved guidance 被记录；
- 一个 snapshot 被冻结；
- 一个 run 引用了该 snapshot；
- 一个 gate report 被写出；
- 一个 claim 有 cited source；
- 一个 feedback issue 被结构化。

它们本身不能证明：

- 模型完全遵守了 guidance；
- 最终文字质量提升；
- 没有有用结构被新 guidance 冲掉；
- 所有相关事实都被覆盖。

这些需要独立 evaluation、reference runs 和未来 manifestation reporting。

## 一分钟演示

面对 IR、合规或业务 reviewer，不要从“multi-agent workflow”讲起。

先打开一份成品。

指着其中一个数字，问：

> 这个数字从哪来的？

然后追溯：

```text
final sentence
-> claim ledger entry
-> source and date
-> gate or audit finding
-> source appendix
-> any approved reader guidance that affected wording
```

这就是 process-level accountability 对用户的含义。

## Related

- `docs/control-surfaces.zh-CN.md`
- `docs/architecture-status.md`
- `docs/support-matrix.md`
- `docs/modules/improvement.md`
- `docs/design-note-preference-taste-governance-2026-06-11.md`
