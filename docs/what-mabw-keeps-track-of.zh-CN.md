# BriefLoop / MABW 会替你记住什么

英文版：`docs/what-mabw-keeps-track-of.md`。

这篇文档只回答一个问题：

> 当你用 BriefLoop 写一份周报、月报、行业简报或 IR 草稿时，系统到底替你记住了什么？

先说结论：BriefLoop 不是单纯“让 AI 写报告”的工具。它更像一套报告生产的记账系统。

AI 可以帮你写草稿；BriefLoop 负责把过程留下来；最后是否采纳、是否交付，仍然由人决定。

MABW 是 BriefLoop 当前保留的运行时和兼容名称。你在命令、包名、历史文件里看到 MABW，通常可以理解为 BriefLoop 的当前实现层。

---

## 你为什么需要它

写一份业务报告，真正麻烦的地方通常不是“生成第一稿”。

真正麻烦的是后面这些问题：

```text
这期报告写到哪一步了？
这个数字、日期、政策、公司事实是从哪来的？
上次 reviewer 提过的写法要求，系统有没有记住？
哪些问题是必须修完才能交付的？
如果 AI 改错了，我能不能追溯回去？
```

BriefLoop 要解决的就是这些问题。

它不会保证报告一定正确，也不会替代人的判断。它做的是：

> 把报告生产过程变成一条可以检查、可以追溯、可以复盘的循环。

---

## 一句话版本

BriefLoop 替你记四本账：

| 账本 | 它回答的问题 | 主要价值 |
|---|---|---|
| 进度账 | 这期写到哪了？ | 中断后能继续，不靠记忆猜 |
| 来源账 | 这个数字哪来的？ | 每个关键事实都能追到来源 |
| 偏好账 | reviewer 要求以后怎么写？ | 只把你批准过的偏好带到未来 |
| 把关账 | 这份报告能不能交付？ | 交付前用 gate 阻断明显问题 |

下面分别讲。

---

## 1. 进度账：这期报告写到哪了

一份报告不是 agent 说“我完成了”就真的完成。

BriefLoop 会记录这一期 run 当前处在哪一步、哪些步骤已经完成、哪些东西还缺、下一步允许做什么。

它主要记录：

- 当前 stage；
- 哪些 stage 已完成、待处理或被阻塞；
- 哪些 artifacts 应该已经生成；
- 哪些 decisions 已经记录；
- Orchestrator 下一步能不能继续。

这解决的是一个很实际的问题：

> 如果一次 run 中断了，或者输出看起来不对，我不用重新猜它刚才做到了哪一步。

你通常可以在这些文件里看到：

```text
output/intermediate/workflow_state.json
output/intermediate/event_log.jsonl
output/intermediate/artifact_registry.json
output/intermediate/runtime_manifest.json
output/intermediate/agent_handoff.md
```

普通用户不需要一开始就读这些 JSON。它们更像黑匣子记录。出问题时，维护者可以靠它们判断：本期到底卡在哪里。

---

## 2. 来源账：每个数字哪来的

业务报告最怕的不是文字不漂亮，而是数字、日期、公司事实、政策状态没有来源。

BriefLoop 会尽量把关键 claim 记录下来，并把它们和来源、日期、检查结果连起来。

它主要记录：

- 报告里的关键 claim；
- 数字、日期、政策、公司事实、价格、产能、客户、项目进度等信息的来源；
- auditor 或 quality gate 对这些 claim 的检查结果；
- 最终交付包里的 source appendix。

它要回答的问题是：

> 在最终简报里随便指一个数字，问“这个数从哪来的？”系统应该能追到对应 claim、source、date 和 checks。

你通常可以在这些地方看到：

```text
output/intermediate/claim_ledger.json
output/intermediate/gates/auditor_quality_gate_report.json
output/intermediate/gates/finalize_quality_gate_report.json
output/intermediate/quality_gate_report.json
output/intermediate/audit_report.json
output/delivery/brief.md
output/delivery/<命名周报>.docx
output/source_appendix.md
output/intermediate/provenance_graph.json
```

这里要注意一个边界：

> 有来源，不等于一定正确；但没有来源的关键事实，不应该假装可靠。

BriefLoop 的作用不是“证明真理”，而是减少黑箱输出，让 reviewer 能检查。

---

## 3. 偏好账：它到底学到了什么

BriefLoop 可以记住一些写作偏好，但它不应该偷偷学习。

最重要的原则是：

> 系统可以观察和建议，但只有你批准过的偏好，才会影响未来 run。

例如 reviewer 说：

```text
以后每条新闻先讲对公司的影响，再讲背景。
不要替管理层下决策。
语气更像 executive brief，不要像研究报告。
不确定的地方先说明不确定性。
```

这些不是事实，而是写作偏好或读者偏好。

BriefLoop 可以把它们整理成 guidance，但只有经过确认后，才会进入未来的 memory snapshot。

你通常可以在这些地方看到：

```text
improvement/ledger.jsonl
improvement/memory.md
output/intermediate/improvement_memory_snapshot.md
output/intermediate/runtime_manifest.json
```

这套机制要保护的是：

- 未批准建议不会影响未来输出；
- 已批准 guidance 只影响后续 run；
- 被撤销的 guidance 应该从后续 snapshot 中消失；
- 系统不能把一次偶然反馈偷偷升级成永久规则。

用更简单的话说：

> AI 可以提建议；系统负责记账；只有你能让偏好生效。

---

## 4. 把关账：什么问题必须在交付前拦住

有些东西不是“写作偏好”，而是交付要求。

例如：

- 关键事实必须有来源；
- source appendix 不能缺；
- 最终读者版不应该出现本地路径；
- 最终读者版不应该出现内部 claim ID；
- 必需 artifacts 没生成时，不能假装已经完成；
- gate 失败时，应该先修复，而不是直接交付。

这些要求应该由 contracts、gates、policies 和 delivery checks 来执行，而不是被软软地写进 memory。

你通常可以在这些地方看到：

```text
configs/orchestrator_contract.yaml
configs/stage_specs.yaml
configs/artifact_contracts.yaml
configs/policy_packs/default.yaml
output/intermediate/gates/auditor_quality_gate_report.json
output/intermediate/gates/finalize_quality_gate_report.json
output/intermediate/quality_gate_report.json
output/intermediate/repair_plan.json
```

这套机制要回答的是：

> 这份报告现在能不能交付？如果不能，到底是哪条检查没过？

---

## 用户反馈应该怎么处理

用户不应该需要判断一句反馈到底属于哪一类。

例如用户说：

> 以后每条新闻先讲对公司的影响，再讲背景，不要替管理层下决策。

系统应该负责拆解，而不是让用户自己区分 taste preference、structure rule、fact correction 或 delivery gate。

可以这样理解：

| 用户说的话 | 系统应该理解成 | 可能进入哪里 |
|---|---|---|
| 以后先讲对公司的影响 | 写作偏好 | memory guidance |
| 每条 news item 都固定三段 | 固定格式候选 | template 或 checkable rule candidate |
| 不要替管理层下决策 | 风格边界 | memory guidance 或 checklist |
| 这个价格/日期/来源错了 | 事实或来源核查 | fact review |
| 来源附录必须有 | 交付检查 | delivery gate |

关键原则：

> 任何会影响未来 run 的持久变化，都应该先让用户看到系统的理解，再由用户确认。

---

## 建议应该默认可见

如果 BriefLoop 从一次 run 中发现了可能有用的偏好或规则，它不应该偷偷记下来。

更好的方式是把候选建议放在一个可见区域，例如：

```text
待你确认的写作偏好
建议加入固定格式的规则
需要核查的事实或来源
系统已经在执行的交付检查
```

用户应该能对这些建议做三件事：

- 确认；
- 编辑；
- 忽略。

同时，候选列表必须容易清空。一个堆满旧建议的 parking lot，会比完全没有建议更伤信任。

---

## 哪些反馈可以批量确认

不是所有确认都有同样风险。

如果用户明确说了一句话，系统只是把它拆成几条，可以允许用户看完后一键确认。

但如果某些偏好是系统从历史样本里推断出来的，风险更高。因为用户并没有亲口说过这些话。

经验规则：

```text
用户明确说过的反馈：可以 grouped review。
系统从样本推断的偏好：必须逐条 review 后再采纳。
```

---

## BriefLoop 不应该声称什么

BriefLoop 有控制面、有账本、有 gate，但这不等于它能自动保证报告质量。

它可以证明：

- 某条 approved guidance 被记录过；
- 某个 snapshot 被冻结过；
- 某次 run 引用了哪个 snapshot；
- 某个 gate report 被写出过；
- 某个 claim 有对应 source；
- 某条 feedback 被结构化处理过。

但它不能单靠这些证明：

- 模型完全遵守了 guidance；
- 最终文字质量一定变好了；
- 所有相关事实都覆盖到了；
- 没有新的 guidance 冲掉原本有用的结构；
- 输出可以不经人工判断直接发送。

这些需要独立 evaluation、reference runs 和人工 review。

特别是 IR、合规、披露、法律或投资相关材料，BriefLoop 只能作为起草和审阅辅助，不能替代专业审查。

---

## 一分钟演示

面对业务 reviewer、IR、合规或管理层，不要从 “multi-agent workflow” 讲起。

直接打开一份最终简报。

指着其中一个数字，问：

> 这个数字从哪来的？

然后追溯：

```text
final sentence
-> claim ledger entry
-> source and date
-> gate or audit finding
-> source appendix
-> approved reader guidance, if it affected wording
```

这就是 BriefLoop 的核心价值：

> 不是让 AI 看起来更聪明，而是让报告生产过程更可问责。

---

## 常见问题

### 这是自动记忆系统吗？

不是。

BriefLoop 可以提出候选偏好，但未经用户确认，不应该影响未来 run。

### 为什么还有这么多 JSON 文件？

因为普通读者看最终报告，系统和维护者需要看运行记录。

这些 JSON 不是给每个业务用户每天阅读的。它们的作用是：出问题时能复盘，交付前能检查，未来 run 能知道自己引用了什么。

### 有了来源账，报告就一定正确吗？

不一定。

来源账只能说明：关键 claim 有可追溯依据，reviewer 可以检查。它不能保证来源本身永远正确，也不能保证模型覆盖了所有重要信息。

### BriefLoop 可以直接用于正式 IR 披露吗？

不能直接这样理解。

它可以帮助准备 IR、市场、战略、合规类报告的草稿和审阅材料，但正式披露仍然需要公司内部审批、律师审查和专业判断。

### 为什么还叫 MABW？

BriefLoop 是项目对外名称。MABW 是历史实现名，也是当前某些命令、包名和文件路径里的兼容名称。

---

## 维护者参考：常见文件位置

普通用户可以先跳过这一节。只有在排查问题、维护 runtime 或检查 gate 时，才需要看这些文件。

### 运行进度

```text
output/intermediate/workflow_state.json
output/intermediate/event_log.jsonl
output/intermediate/artifact_registry.json
output/intermediate/runtime_manifest.json
output/intermediate/agent_handoff.md
```

### 来源和 claim

```text
output/intermediate/claim_ledger.json
output/intermediate/provenance_graph.json
output/intermediate/audit_report.json
output/source_appendix.md
output/delivery/brief.md
output/delivery/<命名周报>.docx
```

### Gate 和修复

```text
output/intermediate/gates/auditor_quality_gate_report.json
output/intermediate/gates/finalize_quality_gate_report.json
output/intermediate/quality_gate_report.json
output/intermediate/repair_plan.json
```

### 用户偏好和 memory

```text
improvement/ledger.jsonl
improvement/memory.md
output/intermediate/improvement_memory_snapshot.md
```

### Contracts 和 policies

```text
configs/orchestrator_contract.yaml
configs/stage_specs.yaml
configs/artifact_contracts.yaml
configs/policy_packs/default.yaml
```

---

## Related

- `docs/control-surfaces.zh-CN.md`
- `docs/architecture-status.md`
- `docs/support-matrix.md`
- `docs/modules/improvement.md`
- `docs/archive/design-note-preference-taste-governance-2026-06-11.md`
