# Architecture Memo: MABW 的三层质量法律——基于 v0.7.1 Solar Reference Run 的实证分析

**日期**: 2026-06-11
**上下文**: Mythos review pack 包含一次基线 run (Run 1) + 一次改进 run (Run 2 Flash) + 一次错模型对照 run (Run 2 v4 Pro)，均为公开太阳能行业内容
**状态**: 审稿人独立分析——不代表示范运行报告，而是对实验证据的质量分层解读

---

## 核心论点

MABW 的"质量"不是一个单一维度。把实验结果摊开看，有三个层次的质判断同时成立，每层证据强度不同，每层天花板也不同。把三层混为一谈会误导，把三层分开才能看到什么是已证明的、什么是正在证明的、什么是尚未证明的。

三层：

1. **法律层（Law）**：确定性门禁——机器能检查的东西，机器确实检查了
2. **诚实层（Honesty）**：交付格式——终稿是否干净、无内部 ID 泄露、读者可读
3. **智慧层（Wisdom）**：分析内容——简报的判断质量是否好、是否比单模型基线好

证据量从第一层到第三层递减。这不是设计缺陷——这是当前版本位置的真实写照。

---

## 实验背景

| | Run 1 | Run 2 (primary) | Run 2 (wrong model) |
|---|---|---|---|
| 模型 | DeepSeek v4 Flash | DeepSeek v4 Flash | DeepSeek v4 Pro |
| AG-0001 | 未 materialize | 已 materialize | 已 materialize |
| 输出结构 | 154行, H2=9, H3=0 | 173行, H2=5, H3=7 | 144行, H2=7, H3=22 |
| 风格 | 中文主题式周报 | 编号章节管理简报 | Card-like 卡片式多层级 |

AG-0001 的改进指令：

> "Foreground possible business implications for a generic U.S. solar module manufacturer before operational follow-up, without making company-specific decisions."

分类：`audience_guidance`，`scope: section`，`level: 2`。人类撰写，人类批准。

来源：`mythos_review_pack_20260611_190232/02_improvement/improvement_ledger.jsonl`，SHA-256 链完整（revision 1→2，previous_revision_sha256 正确链接）。

---

## 第一层：法律层——确定性门禁质量

### 证据

Run 2 的门禁**阻断了三次**才放行：

> "Gates blocked three times on material_fact citation placement before passing; final quality gate report has `status=pass`, `findings=[]`."
> — `06_notes/run_notes.md` 第 40-41 行

Run 1 的审计报告（`03_gates_audit/run1_audit_report.json`）显示 7 项检查全部通过：

- **source_support**: 所有 20 个 claim 均被引用，无孤立引用，无缺失 claim
- **freshness**: 所有 20 个来源均在 14 天窗口内（最旧者 10 天），无过期来源
- **numbers**: 所有数值（66GW、11GW、6:1、1GW+4.5GW、60 个经济体、136MW、20 亿美元、1GW+、1.5GW+2.2GWh、10.6GW、907W、29.2%、6GWh、1.3GW、800MW+240MW+610MW+40MW、49 亿美元、6.6GW）均与 claim ledger 逐项对齐
- **dates**: 每个事件均有日期标注，与来源日期一致
- **advice_safety**: 无投资建议、无法律建议、无交易信号
- **process_residue**: 无第一人称、无 TODO/TBD、无 `[SRC:]` 残留
- **redaction_risk**: 无内部路径、无用户名、无私有数据、无 MNPI

Run 1 和 Run 2 的 `quality_gate_report.json` 均存在，均 report `pass`。`workflow_state.json` 均到达 `current_stage: null`，`last_decision.stage_id: finalize`，`last_decision.decision: finalize`。

### 判断

**法律层 PASS。** 确定性基础设施被验证为在真实规模的运行中正确运作。门禁不是装饰——它在阻截真实问题，在三次阻塞后通过修复放行。

但这是法律层的天花板：它只保证了简报不会犯可被规则捕获的错误（无来源数字、过期来源、目标不相关、投资建议措辞、过程残留），不保证分析质量好。

---

## 第二层：诚实层——读者交付格式

### 证据

**Run 1（无 AG-0001）**：读者输出干净。免责声明完整。方法论与局限章节完整。无内部 ID 泄露——finalizer 成功剥离了 `[src:CLM-XXX]` 引用标记。读者附录正常生成。

**Run 2 Flash（有 AG-0001）**：出现三个读者交付缺陷：

1. **裸内部 ID 泄露**：`[CL-0001]` 到 `[CL-0020]` 出现在 `output/brief.md` 中。Finalizer 剥离了 `[src:...]` 但没有剥离裸 `[CL-XXXX]`。
   - 来源：`04_outputs/run2_flash_brief.md` 第 23、25 等多处

2. **脚注过程措辞泄露**：终稿底部出现 "本报告由Analyst subagent基于Claim Ledger（20条声明）编制"——内部角色名称和系统组件名称暴露在读者输出中。
   - 来源：`04_outputs/run2_flash_brief.md` 第 172 行

3. **引用索引表空白 ID 单元格**：读者可见的引用索引表中 ID 列为空。
   - 来源：`04_outputs/run2_flash_brief.md` 第 147-168 行

**Run 2 v4 Pro（有 AG-0001）**：**以上三个缺陷均未出现。** 输出干净，卡片式结构，无裸 ID，无过程措辞，无空白索引。

### 判断

**诚实层 MIXED。** Run 1 干净。Run 2 Flash 不干净——不可交付给读者。Run 2 v4 Pro 干净。

关键洞察：**AG-0001 的内容指导改善了一个维度的质量（商业 implication 前置），同时在一个特定模型（Flash）上引入了另一个维度的质量退化（裸 ID 泄露 + 过程措辞残留）。** Pro 没有出现同等问题，说明这是一个 guidance × model sensitivity 交互效应，不是 guidance 本身的系统性问题。

这是 MABW 参考运行事后分析中 Issue #6（Guidance-Induced Regression）的第二个活标本——而且是跨模型版本的。它暴露了一个门禁 gap：当前的门禁检查 process_residue 但没有检查裸 `[CL-XXXX]` 模式。门禁知道剥离 `[src:...]`（第 51 个 marker 被剥离），但 `[CL-0001]` 不是 `[src:...]`——它不在 finalizer 的剥离正则里。

---

## 第三层：智慧层——分析内容质量

### 证据

实验报告自己记录了严格的 confounder：

```text
candidate_claims SHA-256:
  run1:  b623dc...f03f9
  run2:  3e056...ce0da
  v4pro: d3fe9...53153b

screened_candidates SHA-256:
  run1:  34102...c51b5
  run2:  2979c...99309
  v4pro: 45fc4...da416

claim_ledger SHA-256:
  run1:  cd7c4...de60
  run2:  d6701...13d3d
  v4pro: a4c7c...4c9d
```
— `06_notes/experiment_report.md` 第 78-93 行

三次 run 的 claim layer 全部不匹配。Run 1 和 Run 2 之间的差异不只是 "guidance 有无"——还有进入 Analyst 的事实主张集合不同。

从审稿人角度，这意味着：run1 vs run2 的输出差异不能严格归因于 AG-0001。实施报告正确地判了 B+："materialized guidance and observed manifestation, with source/claim-layer drift as a confounder"（`experiment_report.md` 第 69 行）。

### 观察到的内容差异（qualified as observational only）

定性而言，Run 2 Flash 和 Run 2 v4 Pro 都比 Run 1 表现出更明显的管理简报特征：

- Run 1：主题式中文周报（H2=9，无子标题），结构接近传统新闻综述
- Run 2 Flash：编号章节管理简报（H2=5, H3=7），包含风险矩阵表、持续跟踪信号清单、关键指标汇总表、引用索引
- Run 2 v4 Pro：Card-like 卡片式输出（H2=7, H3=22），执行摘要中使用 ▸ bullet，风险与合规独立章节，展望与建议关注清单

内容前沿化与 AG-0001 的指令方向一致（"foreground business implications before operational follow-up"）。但 confounded 实验设计意味着只能做定性观察，不能做因果声明。

### 判断

**智慧层 NOT MEASURED。** 不是因为不重要——它最重要——而是因为当前实验设计（claim layer 不冻结）无法支撑它。这是 v0.8 的 baseline 实验要填补的空白。

---

## 从外部审稿人角度，这个实验最值钱的是什么

**不是「MABW 比 baseline 好」，而是以下三个发现。**

### 发现一：门禁是活的控制面，不是装饰性 pass

三次阻截 → 修复 → pass 的循环是 MABW 核心论点——"写成机器的部分没坏"——在真实规模运行中的验证。门禁不是一个脚本，它确实在阻截真实问题。这是第一层质量（法律层）的证据，且证据强度为 A 级：有 SHA-256 记录、有 event log timeline、有 gate_report 状态变化序列。

### 发现二：Improvement Ledger → Snapshot → Manifestation 链路闭合

从 `propose` → `approve` → SHA-256 链 → `materialized_entry_ids: ["AG-0001"]` → `improvement_memory_snapshot.md` → 运行时可见，这条链路在真实运行中闭合了。人类批准的 guidance 进入了运行上下文。Manifestation（输出中出现更多商业 implication 语言）可以被观察到，但因果归属有 confounder。

### 发现三：Guidance-Induced Regression × Model Sensitivity 的活标本

AG-0001 改善了一个维度的质量（商业 implication 前置），同时在 Flash 模型上引入了另一个维度的质量退化（裸 CL-ID 泄露 + 过程措辞残留）。Pro 模型没有这个问题。这是 DRA regression（参考运行事后分析 Issue #6）的跨模型实例，暴露了两个具体 gap：

1. **Finalizer 剥离覆盖不全**：`[src:...]` 被剥离了，`[CL-XXXX]` 没有
2. **门禁缺少裸 ID 检测**：当前的 process_residue 检查不包含裸 claim ID 模式

这两个 gap 是具体的、可修的、不需要重新设计架构。

---

## 质量分层的操作含义

三层质量法律暗示三层修复优先级：

| 层 | 当前状态 | 下一步 |
|---|---------|--------|
| 法律层 | PASS（证据强度 A） | 维持；修门禁 gap（裸 ID 检测、过程措辞检测） |
| 诚实层 | MIXED（model-sensitive） | 修 finalizer 剥离；加 reader-output validation gate；跨模型一致性检查 |
| 智慧层 | NOT MEASURED | 冻结 claim layer → 严格 controlled run → baseline comparison |

这不是三个独立的问题，而是一个递进：**先保证法律层稳（门禁不装饰），再保证诚实层干净（交付给读者的东西不含内部垃圾），再在干净的基础上测智慧层（MABW 是否真的让质量变好）。** 在诚实层都不干净的时候用输出跑 baseline comparison，变量太多了。

---

## 关于跨模型敏感性的初步观察

Flash 和 Pro 之间的输出差异（裸 ID 泄露 vs 干净输出、H3=7 vs H3=22、编号章节 vs 卡片式）本身就是一个值得记录的发现：**相同 guidance、相同 workspace、相同 CL pipeline，不同模型产生结构上显著不同的输出来满足同一条 guidance。**

这不是 MABW 的缺陷——同一个工单给两个不同的分析师，他们也会用不同结构来完成。但它对评估设计有直接含义：guidance manifestation measurement 必须跨模型做，不能只在一个模型上跑然后声称 "guidance works"。否则你分不清是 guidance 的效果还是模型的特征。

这支持了参考运行事后分析 Issue #7（"Runtime/Model Differences — Unrecorded"）的 v0.8 修复方向：模型身份必须进入 manifest schema，跨模型对比必须成为 baseline 实验的标准维度。

---

## 与 MABW 三层质量的关系

实验报告显式拒绝了质量过高的声明：

> "This run does not prove output quality improvement. It does not prove stable manifestation across models. It does not prove strict causality because the claim layer drifted between runs."
> — `experiment_report.md` 第 149-154 行

这是诚实的。从外部审稿人的角度，这组实验最正确的是：**它没有用门禁通过来暗示简报质量好，也没有用 guidance materialized 来声称质量改善。** 它把每一层的证据强度都标清楚了——法律层可以给 A，诚实层给 B（有已知 gap），智慧层标注了 NOT MEASURED。

---

## 建议的下一步（审稿人视角）

1. **立即修**：Finalizer 加裸 `[CL-XXXX]` 剥离；门禁加裸 ID 检测和过程措辞检测。这两个 fix 是纯增量的，不碰架构。

2. **下一次 controlled run**：在 claim layer 冻结后跑（复用同一组 `candidate_claims.json` / `screened_candidates.json` / `claim_ledger.json`），变量只剩 guidance snapshot。这是 A-grade controlled claim 的前提。

3. **保留 v4 Pro 对照样本**：作为跨模型 sensitivity 的证据。不要丢弃。

4. **门禁 schema 记录模型身份**：`runtime_manifest.json` 已有 `improvement` 块，可以加模型/运行时身份字段。参考运行事后分析 Issue #7。

---

*Architecture Memo 2026-06-11。基于 Mythos review pack (mythos_review_pack_20260611_190232.zip) 中的 v0.7.1 Public Solar Reference Run 实验数据。证据来源包括 53 个文件中的 4 份输出简报、2 份审计报告、2 份门禁报告、2 份 event log、2 份 workflow state、3 组 claim layer 产物、1 份改进账本、1 份实验报告和 1 份运行笔记。*
