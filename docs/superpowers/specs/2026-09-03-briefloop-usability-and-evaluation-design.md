# BriefLoop 可用性与评测栈设计

- 日期：2026-09-03
- 基线：`main` @ `3f1334e8`
- 状态：待评审
- 关联：WikiSkill (arXiv 2608.27454v1)

## 1. 问题陈述

BriefLoop 当前不是一个可用产品。这不是主观判断，可以量化。

### 1.1 主路径自己声明自己不可用

`CLAUDE.md` 写明当前唯一运行时是 SQLite-only Codex ControlStore 路径。
`docs/support-matrix.md` 给这条路径的状态是 **Experimental**。

| 指标 | main @ 3f1334e8 |
|---|---|
| Supported | 50 |
| **Experimental** | **36** |
| Retired | 19 |
| Deprecated | 4 |
| 文档中 `NOT MEASURED` 出现次数 | 47 |
| `src/` | 294 文件 / 146,096 行 |
| `tests/` | 165 文件 / 98,062 行 |

### 1.2 首跑体验把证据放在了产品前面

`docs/15-minute-pilot.md`（74 行）的定位是"在读架构文档之前先看看 BriefLoop"。
其中：

- "Inspect These Three Files" 表指向 `quality_panel.html`、`quality_summary.md`、
  `claim_ledger.json` — 三个审计面，**不含 brief 本身**
- "What BriefLoop Is Not" 独占一节，5 个 bullet，末尾另加一句
  `In short: BriefLoop is not a semantic proof engine.`

`scripts/demo.py` 的成功输出确实把 `output/delivery/brief.md` 列在第一位，
但紧接着并列 6 个审计文件，随后是两段免责声明。**产品被证据淹没，不是缺席。**

### 1.3 产生证据的能力被删掉了，声明"没有证据"的文字留了下来

这是 36 个 Experimental 和 47 个 `NOT MEASURED` 无法消解的机械原因。

`src/multi_agent_brief/evaluation_cases/` 下有 24 个带 ground truth 的评测 case：

```yaml
case_id: unsupported_material_fact
expected:
  findings_any:
    - finding_type: number_without_source
      blocking_level: blocking
  workflow_state: { blocked: true }
```

10 种 `finding_type`，8 个 must-block + 5 个 must-not-block。但：

| 组件 | 状态 |
|---|---|
| runner | 已删（LD2-3） |
| `briefloop eval-cases list/validate/run` | Retired (LD2-3) |
| `evaluation_cases/` 下的 `.py` 文件 | 1 个，371 字节的空壳 `__init__.py` |
| 唯一引用它的测试 | `test_evaluation_and_onboarding_modules_do_not_import_cli_layer` |

那个测试遍历 `evaluation_cases/**/*.py` 检查 import 边界——目录里只有空壳，
**对 24 个 case 完全空转**。

`experiments/080/` 的三条件 A/B harness 同样全部退役：
`validate-case`、`register-run`、`score-run`、以及整个 080 测试套件。
数据文件还在，读数据的代码一行不剩。

`docs/support-matrix.md` 对这批孤儿数据的说法是
"preserved for the **EF-1/EF-2** Store-native evaluation rebuild"。
全仓 grep `EF-1`/`EF-2` 共 4 处：CHANGELOG 1 次、support-matrix 2 次、
`__init__.py` 1 次。**全部自我引用，无设计文档、无 roadmap 条目。**

这是一个名字，不是计划。

### 1.4 即便有 runner，这 24 个 case 也不能做闭环的奖励信号

manifest 中 21 种 action 全是确定性 Python 调用：

```
28 state.check   10 gates.check   6 status.show   6 state.decide
6 runtime.run_handoff   5 synthetic.seed_claim_support_case   4 release.check ...
```

**没有一个 action 触发 agent 角色。** 它们验证的是 Python 门控层，
不是角色指令。作为 WikiSkill 的 ℛ 使用时，这些 case 测不到被演化的对象。

### 1.5 发散仍在继续

同期并行分支：

```
main                            36 Experimental
feat/debrand-packaged-defaults  36 Experimental
feat/dsh-runtime-0.16.0         38 Experimental   ← 新增第二套 runtime kit
```

`feat/dsh-runtime-0.16.0` 新增 `runtime_kits/dsh/`（host/client plugin +
8 个角色 preset + `runtime_host_v2/dsh.py`），与 Codex kit 并列，VERSION 仍为 `0.15.3`。
收敛压力目前小于扩张压力。

## 2. 目标与非目标

### 目标

**单人首次出活**：一个陌生人 clone 之后，15 分钟内不读架构文档，产出一份带来源标注的简报。

### 非目标（本设计明确不做）

- 不改任何不变量：append-only receipts、事务原子性、哈希绑定、`guidance_binding_invalid` 一律不动
- 不删任何 Experimental 代码、测试或 contract（只从默认表面隐藏）
- 不做 workspace 内的用户自维护知识层
- 不做运行时自动学习
- 不实现 WikiSkill 闭环本身（见 §7）

### 关于"降低研究严谨性"

本设计**不降低任何不变量的严谨性**。唯一降低的是**严谨性的展示密度**：
47 处 `NOT MEASURED` 与 17 条红线内容一字不删，仅从首跑路径搬到专门页面。

真正的问题不是严谨性过高，而是**严谨性摊得太薄**——WikiSkill 对 1 个主张极度严谨
（5 benchmark / 3 次独立运行 / paired bootstrap / 63.7 vs 60.9 的消融数字），
BriefLoop 对 84 个能力严谨地拒绝主张。方案 B′ 的目的正是把严谨性收缩到可测的一条上。

## 3. 方案 A：翻转首屏

### A1. `docs/15-minute-pilot.md`

- "Inspect These Three Files" → 改为先看 `output/delivery/brief.md` 与
  `output/source_appendix.md`，审计面移至"想深究再看"小节
- "What BriefLoop Is Not" 整节移出，正文保留一句指向 `docs/claims.md` 的链接

### A2. `scripts/demo.py::_print_success`

输出分两段：

```
Your brief:
- <workspace>/output/delivery/brief.md
- <workspace>/output/source_appendix.md

How it was checked (optional):
- quality_panel.html / claim_ledger.json / quality_summary.md / ...
```

免责声明缩为一行 + `docs/claims.md` 链接。行为不变，仅调整呈现。

### A3. 新建 `docs/claims.md`

汇总当前散落的声明边界：47 处 `NOT MEASURED`、README "Evidence boundary" 节、
pilot 的 "What BriefLoop Is Not"。**内容不删减，集中一处。**

### A4. `README.md` 676 行 → 一屏

保留：一句话定位、judge quickstart、产出物、四个可追溯问题、文档索引。
移出：Build Week 表格 → `docs/build-week.md`；Evidence boundary → `docs/claims.md`；
v0.15.3 successor/guidance 段落 → `docs/support-matrix-experimental.md`。

### A5. Experimental 从默认表面隐藏

`src/multi_agent_brief/cli/main.py` 的分组注释已标出天然接缝：

```python
# Experimental measurement harnesses
experiments_commands.register(subparsers)      # experiments {a2-isolation-preflight, laj, 080}

# Experimental product-layer report contracts
product_commands.register_*(subparsers)        # new / packs / validate-report-spec / extract / quality
```

- 默认 `--help` 不列出这两组；`BRIEFLOOP_EXPERIMENTAL=1` 或 `--experimental` 时列出
- 直接调用仍然有效（不破坏现有脚本），但打印一行 Experimental 提示
- `docs/support-matrix.md` 主表只保留 50 个 Supported；36 个 Experimental 与
  19 个 Retired 移至 `docs/support-matrix-experimental.md`，主表首行给出链接

### A6. 验收标准

- 陌生人执行 `bash scripts/demo.sh` 后，终端第一屏出现的第一个路径是 `brief.md`
- `briefloop --help` 输出不含 `experiments`、`new`、`packs`、`extract`、`quality`
- `README.md` ≤ 120 行
- `rg -c "NOT MEASURED" README.md docs/15-minute-pilot.md` = 0
- 全部现有测试通过，无测试被删除或跳过

## 4. 方案 B′：agent-rollout 评测栈

填掉 `EF-1/EF-2` 这个空名字。**这是 47 处 `NOT MEASURED` 唯一的出口。**

### B′1. 为什么现在是最好的时机

旧 runner 已被 LD2-3 删净，**没有兼容负担**。而已完成的标注规格（24 个场景、
10 种 `finding_type`、8:5 的 must-block/must-not-block 配比）是可直接复用的资产。

### B′2. Case 格式

```yaml
case_id: stale_source_in_weekly_pack
synthetic: true
input:
  source_pack: cases/stale_source_in_weekly_pack/sources/     # 合成 source pack
  report_date: "2026-06-08"
rollout:
  role: auditor            # 跑 agent 角色，非 Python gate
  runtime: codex
ground_truth:
  seeded_defects:
    - defect_id: d1
      finding_type: stale_source
      locator: "source-002.md#L14"
  clean_claims:            # 不得被误杀的真实声明
    - claim_locator: "source-001.md#L8"
expected:
  must_block: true
```

关键与旧格式的差异：`rollout.role` 触发 agent，`ground_truth` 同时标注
**该抓的缺陷**与**不该误杀的真声明**。

### B′3. ℛ 的定义

```
ℛ = defect_recall(must-block cases) × true_negative_rate(must-not-block cases)
```

**乘法，不是加法。** 全部拦截 → 第二项归零；全部放行 → 第一项归零。

这使红线 `Precision-only quality gate ... can reward omission. Pair with
coverage-side checks` 被**结构性满足**，而非靠承诺满足。这也是上游讨论中
"用 Gate 通过率当 ℛ 会演化出靠省略过闸门的 skill"这一反对的解法。

### B′4. 规模

24 个不够。ℛ 的粒度是 case 级 pass/fail：24 个 val case 下翻转 1 例 = 4.2 个百分点，
噪声淹没信号。40 个 val case 降到 2.5 个百分点，是可接受下限；
若 B′7 的重测方差仍大于 2.5pp，扩容语料而非上门控（见 §8）。

| 项 | 目标 |
|---|---|
| 总量 | 80 case（40 train / 40 val）；40 个 val case 下翻转 1 例 = 2.5 个百分点 |
| 配比 | 沿用 8:5，即约 60% must-block / 40% must-not-block |
| 每 case | 1 份合成 source pack + 1–3 个埋入缺陷 + 标注的 clean claims |
| 覆盖 | 现有 10 种 `finding_type` 全覆盖，每种 ≥ 4 例 |

### B′5. 构造方法

1. 以 24 个现有 case 的 `description` 与 `expected` 为模板
2. source pack 由模型批量生成，**人工校验缺陷标注**（标注是 ground truth，不能自动化）
3. 全部合成，遵守红线：不含私有 prompt、真实 URL、token、商业 benchmark case
4. 遵守 `Do not execute arbitrary shell strings from evaluation fixtures`：
   case 仅声明式描述 role 与输入，不含 shell 串

### B′6. Runner

新建 `src/multi_agent_brief/evaluation_v2/`（不复活已退役的 `eval-cases` 名称）：

- `contracts.py` — case schema（strict DTO，与仓内 contract 风格一致）
- `runner.py` — 按 case 触发一次 agent rollout，收集 findings
- `scoring.py` — 计算 defect_recall / true_negative_rate / ℛ
- CLI：`briefloop eval run|score|report`，注册在 `--experimental` 分组下

### B′7. 成本

| 项 | 估计 |
|---|---|
| 语料标注 | 1–2 人周 |
| runner + scoring | 1–2 周 |
| 每轮 ℛ 评估 | 60–80 次真实模型调用 × rollout 次数（唯一持续开销） |

### B′8. 验收标准

- `BRIEFLOOP_EXPERIMENTAL=1 briefloop eval run --split val` 在 main 上产出一个 ℛ 数字
- 该数字可复现（同一 case 集 + 同一角色版本 → 同一 ℛ，或报告方差）
- `docs/claims.md` 中至少 1 处 `NOT MEASURED` 被替换为带数字的 measured claim
- 60–80 个 case 全部合成、公开安全

## 5. 与 WikiSkill 的对应关系

### 5.1 借了什么

| WikiSkill | 本设计 | 落点 |
|---|---|---|
| 产物是明文、一眼可读 | 首屏先给 brief，审计面降级 | A1–A2 |
| `index.md` 单一入口 | README 一屏 + 分层文档索引 | A4–A5 |
| 对 1 个主张极度严谨 | 收缩到 1 个可测 ℛ | B′3 |
| 验证集 + 门控 | 60–80 case 的 train/val split | B′2–B′4 |

### 5.2 没借什么，以及为什么

**闭环本身**（Skill Proposer + `ℛ_val > ℛ_best` 门控）不在本设计范围内，
因为门控的前提是 ℛ 存在，而 ℛ 在 B′ 完成前不存在。见 §7。

### 5.3 关于 OfficeQA 的可行性论证

WikiSkill 在 SpreadsheetBench 与 OfficeQA 上有效，不能直接推出
"BriefLoop 场景可行"。五个 benchmark 的载荷属性不是领域，是**有答案 key**：

| Benchmark | ℛ |
|---|---|
| SpreadsheetBench | 目标单元格状态 → 精确匹配 |
| OfficeQA | 标准答案 → 准确率 |
| ALFWorld | 目标谓词 → 满足/否 |
| SealQA / LiveMathematicianBench | 答案 key |

"写本周简报"没有答案 key——两个称职的分析师写出两份不同的、都好的简报。

**但任务可分解，且一半子任务有答案：**

| 子任务 | 有答案 key | 可否作 ℛ |
|---|---|---|
| 这个数字在源文件里吗 | 是 | 可 |
| 这个源过期了吗 | 是 | 可 |
| 这条声明有来源支撑吗 | 大部分 | 可 |
| 简报缺不缺 limitation 节 | 是 | 可 |
| 标题范围与内容是否相符 | 是 | 可 |
| 有没有无支撑的最高级表述 | 是 | 可 |
| 这段分析写得好不好 | 否 | 不可 |

现有 10 种 `finding_type` 按 `gate_stage_id` 恰好分成两组，印证了这个划分：

| gate_stage_id | finding_type |
|---|---|
| `auditor`（4 种） | `number_without_source`、`stale_source`、`claim_support_matrix_blocking_support`、`target_priority_claim_missing_from_summary` |
| `finalize`（6 种） | `target_relevance_gap`、`final_incomplete_key_case_fields`、`final_missing_comparison_basis`、`final_missing_limitation_section`、`final_scope_title_mismatch`、`final_unsupported_superlative` |

**关键结论：可测边界不是按角色划的，是按"结构性缺陷 vs 主观质量"划的。**

6 种 `final_*` 缺陷针对的正是 Analyst / Editor 的输出，且全部机器可判——
"缺 limitation 节"、"标题与范围不符"、"无支撑的最高级"都有答案 key。
不可测的只有"这段分析写得好不好"这一类主观判断。

因此 ℛ 覆盖 Scout / Screener / Claim-Ledger / Auditor / Editor 五个角色的
**结构性输出契约**，仅排除主观文风质量。B′ 的语料据此设计，
`auditor` 与 `finalize` 两组缺陷都要覆盖。

## 6. 红线核对

`docs/red-lines-and-anti-patterns.md` 共 17 条红线 + 11 个反模式。逐条核对：

| 红线 | 本设计 |
|---|---|
| `Do not turn briefloop run into a Python brief generator` | 不触发；不新增生成路径 |
| `Do not tell users "the system learned this"` | 不触发；A/B′ 不含学习 |
| `no automatic path from issue/Gate finding to successor snapshot` | 不触发；不碰 successor |
| `LLM-as-judge default eval` | 不触发；ℛ 是确定性 finding 匹配 |
| `Do not execute arbitrary shell strings from evaluation fixtures` | **需遵守**，见 B′5.4 |
| `Do not ship private prompts / real URLs / tokens as public fixtures` | **需遵守**，见 B′5.3 |
| `Precision-only quality gate can reward omission` | **结构性满足**，见 B′3 |
| `Provenance projection ≠ semantic proof` | 不触发；ℛ 不声称语义证明 |
| 其余 9 条 | 均只约束声明措辞或已退役路径，不触发 |

**零违反。**

### 一个必须澄清的分类错误

`utility is NOT MEASURED` 常被当作红线引用。它不是红线，是**一次没做的测量**。
`experiments/080/` 的三条件 harness 正是为消除它而建，命令写好了，从未运行，
随后连工具链一起退役。

**挡住产品声明的不是红线，是缺失的测量。** B′ 就是补这次测量。

## 7. 明确不做：B″（WikiSkill 闭环）

在 B′ 产出第一个 ℛ 之前**不设计、不实现**。

理由：没有 ℛ 就没有门控，没有门控 WikiSkill 退化为"让模型随便改提示词"。

若 B′ 完成后决定推进，形态应为：

- **离线、repo 侧、人 merge 的 CI 工具**，不是运行时特性
- 被演化对象 = `configs/agent_roles.yaml`（781 行）
  → 生成 `runtime_kits/codex/`（11 文件 / 520 行 md）
- wiki 层为明文 markdown，位于 repo 而非 workspace
- 每轮产出一个 PR diff，由人评审合并

WikiSkill 的循环本就是**训练期程序**：产出 skills，skills 再发布。
映射到 repo 侧后，客户 runtime 一行不改，"系统学会了"这句话永远不必说——
学习发生在 PR 里，用户拿到的是版本。所有红线因此天然不触发。

**注意**：`feat/dsh-runtime-0.16.0` 合入后 skills 层将有两套 kit
（Codex + dsh），共同生成源仍是 `agent_roles.yaml`。演化一处，两个 runtime 受益。

## 8. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| B′ 测出当前角色指令已够好，演化空间很小 | B″ 不值得做 | B′ 独立有价值——它是 `NOT MEASURED` 唯一出口；接受这个结果 |
| 60–80 case 仍不足以让 ℛ 稳定 | 门控噪声大于信号 | 先在 val 集上测 ℛ 的重测方差，方差过大则扩容而非上门控 |
| 合成 source pack 与真实简报分布不符 | ℛ 提升不迁移到真实使用 | 从 `experiments/080/` 的冻结事实层取真实结构做模板 |
| A 与 `feat/dsh-runtime-0.16.0` 冲突 | 隐藏清单对不上 | A5 按分组接缝实现，新 runtime 归入同一 Experimental 分组即可 |
| 隐藏 Experimental 破坏现有用户脚本 | 回归 | 命令仍可直接调用，仅从 `--help` 隐藏 |

## 9. 实施顺序

A 与 B′ 可并行——A 改文档与 CLI 表面，B′ 建评测栈，无文件冲突。

```
A  (1–2 周, 可逆)  ─┐
                    ├─→  B″ 决策点（看 B′ 的第一个 ℛ）
B′ (3–5 周)        ─┘
```

B′ 不阻塞 A。A 不阻塞 B′。B″ 在两者之后单独决策。
