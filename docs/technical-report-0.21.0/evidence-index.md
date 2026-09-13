# BriefLoop 0.21.0 技术报告：证据索引

文档版本：正式 v3，2026-09-14。实现证据以 main `ddc6583890bf9ddc09c71bd8e078dbc08c2a0dc0` 为准，源码链接固定到与其 tree 一致的发行提交 `847452bfb39e4185e5d91cc537e030c8e4d7da69`，不随 main 后续移动。

[源码哈希](source-manifest.json) · [发行核对摘要](release-verification.json) · [报告正文](technical-report.md)

<a id="e01"></a>
## E01 · 任务与企业背景入口

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

任务约束、冻结内容方法和稿件接纳路径；不据此判断模型实际完成质量。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/store.py` | [`def create_run` · 216](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/store.py#L216) |
| `src/briefloop/runtime.py` | [`def generate(` · 852](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/runtime.py#L852) |

<a id="e02"></a>
## E02 · 程序与 Agent 的职责

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

生成输入与受控操作接纳；宿主原生能力不等于统一受控接口。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/runtime.py` | [`def generation_prompt` · 156](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/runtime.py#L156) |
| `src/briefloop/research_plan.py` | [`def admission` · 147](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/research_plan.py#L147) |

<a id="e03"></a>
## E03 · 读者约定和企业内部写作

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

本轮约定及写作指令；指令存在不等于产物已遵守。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/deliverable_spec.py` | [`def resolve` · 55](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/deliverable_spec.py#L55) |
| `src/briefloop/deliverable_spec.py` | [`def instructions` · 146](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/deliverable_spec.py#L146) |
| `src/briefloop/workflow_assets/business_report/writing.md` | [`先完成` · 1](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/workflow_assets/business_report/writing.md#L1) |

<a id="e04"></a>
## E04 · 内容方法快照

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

四种方法家族、角色投射与快照哈希；学习 trial 的一致性另见 E16。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/document_workflows.py` | [`def resolve_workflow` · 23](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/document_workflows.py#L23) |
| `src/briefloop/document_workflows.py` | [`def freeze_workflow` · 46](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/document_workflows.py#L46) |
| `src/briefloop/document_workflows.py` | [`def workflow_context` · 62](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/document_workflows.py#L62) |

<a id="e05"></a>
## E05 · DOCX 模板准备与导出

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

从准备底稿加载、校验原件、按章节和样式渲染。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/templates.py` | [`def import_template` · 28](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/templates.py#L28) |
| `src/briefloop/templates.py` | [`def prepare(` · 249](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/templates.py#L249) |
| `src/briefloop/templates.py` | [`def export_template` · 333](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/templates.py#L333) |

<a id="e06"></a>
## E06 · 研究档位和轮次状态

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

正式预设和事务内计划变更；轮次槽位不等于已完成工作。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/research_plan.py` | [`PRESETS =` · 22](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/research_plan.py#L22) |
| `src/briefloop/research_plan.py` | [`def freeze(` · 88](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/research_plan.py#L88) |
| `src/briefloop/research_plan.py` | [`def _save_plan` · 226](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/research_plan.py#L226) |
| `src/briefloop/research_plan.py` | [`def begin_round` · 266](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/research_plan.py#L266) |
| `src/briefloop/progress.py` | [`def ` · 12](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/progress.py#L12) |

<a id="e07"></a>
## E07 · 共享预算

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

受控搜索和正文页的预算；不度量整任务模型 Token 或全部原生网络。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/research_budget.py` | [`def reserve_search` · 118](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/research_budget.py#L118) |
| `src/briefloop/research_budget.py` | [`def reserve_pages` · 158](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/research_budget.py#L158) |

<a id="e08"></a>
## E08 · Scout 结果与轮间摘要

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

来源归属、定位语法、轮间交接和部分读取；语法通过不证明原文支持结论。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/scout_tools.py` | [`def join_scouts` · 29](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/scout_tools.py#L29) |
| `src/briefloop/scout_tools.py` | [`def check_handoff` · 89](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/scout_tools.py#L89) |
| `src/briefloop/scout_tools.py` | [`def read_source` · 132](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/scout_tools.py#L132) |
| `src/briefloop/runtime.py` | [`def _research_handoff` · 129](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/runtime.py#L129) |

<a id="e09"></a>
## E09 · 搜索接口与 DDG

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

冻结提供方、统一预算、失败分类与 DDG HTML 搜索适配；发行联网遇 TLS 失败。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/websearch.py` | [`def provider_for_run` · 74](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/websearch.py#L74) |
| `src/briefloop/websearch.py` | [`def search(` · 90](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/websearch.py#L90) |
| `src/briefloop/websearch.py` | [`def extract(` · 152](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/websearch.py#L152) |
| `src/briefloop/duckduckgo.py` | [`def validate_search` · 91](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/duckduckgo.py#L91) |
| `src/briefloop/duckduckgo.py` | [`def call_search` · 109](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/duckduckgo.py#L109) |

<a id="e10"></a>
## E10 · MCP 报告授权与来源接纳

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

资源/工具选择与结果存储；存在接口不代表某供应商已通过真实业务测试。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/connectors/materials.py` | [`def freeze(` · 69](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/connectors/materials.py#L69) |
| `src/briefloop/connectors/materials.py` | [`def revoke(` · 113](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/connectors/materials.py#L113) |
| `src/briefloop/connectors/materials.py` | [`def _acquire` · 139](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/connectors/materials.py#L139) |
| `src/briefloop/connectors/materials.py` | [`def _admit` · 171](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/connectors/materials.py#L171) |

<a id="e11"></a>
## E11 · 写前证据对照

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

九种关系、examined/unexamined 划分和输入身份；语义关系由 Agent 判断。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/reconciliation.py` | [`RELATIONS =` · 16](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/reconciliation.py#L16) |
| `src/briefloop/reconciliation.py` | [`def candidates` · 45](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/reconciliation.py#L45) |
| `src/briefloop/reconciliation.py` | [`def save(` · 131](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/reconciliation.py#L131) |
| `src/briefloop/reconciliation.py` | [`def read(` · 189](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/reconciliation.py#L189) |

<a id="e12"></a>
## E12 · 版本化审阅包

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

snapshot_version=7 包含来源陈述、对照和 Fact Checker 候选，保存对目标版本的适用身份。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/review.py` | [`def _snapshot` · 248](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/review.py#L248) |
| `src/briefloop/review.py` | [`def build_packet` · 464](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/review.py#L464) |
| `src/briefloop/review.py` | [`def validate_applicable_review` · 206](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/review.py#L206) |
| `src/briefloop/review.py` | [`def run_review` · 730](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/review.py#L730) |

<a id="e13"></a>
## E13 · 正式交付规则

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

父要求及条款 ID 分别映射；核心主张核查与内容条款 unverified 提示是不同规则。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/release.py` | [`def decision` · 60](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/release.py#L60) |
| `src/briefloop/deliverable_spec.py` | [`def requirement_severity` · 20](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/deliverable_spec.py#L20) |
| `src/briefloop/release.py` | [`def eligibility` · 247](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/release.py#L247) |

<a id="e14"></a>
## E14 · 富文档模型

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

受限 Tiptap JSON、Markdown 投影及显式转换；不承诺任意富文档结构无损。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/document_model.py` | [`BLOCKS =` · 9](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/document_model.py#L9) |
| `src/briefloop/document_model.py` | [`def normalize_document` · 60](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/document_model.py#L60) |
| `src/briefloop/document_model.py` | [`def brief_document` · 269](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/document_model.py#L269) |

<a id="e15"></a>
## E15 · 版本绑定的 Word 作业

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

相同输入查询与入队在同一事务；已完成文件校验存在性和哈希，损坏可重建。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/export_jobs.py` | [`def export_input` · 11](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/export_jobs.py#L11) |
| `src/briefloop/export_jobs.py` | [`def enqueue_export` · 31](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/export_jobs.py#L31) |
| `src/briefloop/export_jobs.py` | [`def generate_word` · 76](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/export_jobs.py#L76) |

<a id="e16"></a>
## E16 · 学习比较的条件

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

方法内容哈希、内部学习克隆、共同执行条件及实际生成版本核对；不是质量收益保证。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/learning.py` | [`def _generate_trial` · 185](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/learning.py#L185) |
| `src/briefloop/learning.py` | [`def _baseline_for_attempt` · 292](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/learning.py#L292) |
| `src/briefloop/learning.py` | [`def learn(` · 324](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/learning.py#L324) |
| `src/briefloop/store.py` | [`def create_run` · 216](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/store.py#L216) |

<a id="e17"></a>
## E17 · 外部 Agent 操作

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

request_id、事务回执、base_version 与工作稿导出；不是远程 MCP 服务端。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/external_requests.py` | [`def capabilities` · 53](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/external_requests.py#L53) |
| `src/briefloop/external_requests.py` | [`def _operation` · 58](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/external_requests.py#L58) |
| `src/briefloop/external_requests.py` | [`def dispatch` · 131](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/external_requests.py#L131) |

<a id="e18"></a>
## E18 · 已集成的可靠性修复

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

这些路径已进入 v0.21.0；历史维护记录不再被当作发行树外的待合并实现。具体运行范围见 E21、E35。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/release.py` | [`severity.update` · 90](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/release.py#L90) |
| `src/briefloop/export_jobs.py` | [`def enqueue_export` · 31](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/export_jobs.py#L31) |
| `src/briefloop/learning.py` | [`def validated_workflow` · 120](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/learning.py#L120) |
| `src/briefloop/document_model.py` | [`def normalize_document` · 60](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/document_model.py#L60) |

<a id="e19"></a>
## E19 · W5 同条件学习单例

**证据类型：** 开发任务已保存的真实模型记录；本文作者核对 verification.json，未重跑模型。

- 原记录源码身份：`b84ef3ad`。
- 模型回执：`deepseek/deepseek-flash`；原生会话数 12。
- 基线正文 SHA256：`0f1d5b43ea126892af5540a03aaf5345e7bbe724bca01957d3c60acd6931c14a`。
- 候选正文 SHA256：`612323165f4c5a6f8995732a82e479800900d1c5280a79a2abfa1d17ebc92941`。
- 比较条件 SHA256：`18fda1cb9a95d77a09a4153cd2f3acd5d26dd0c62230241c5802199fc4ea0263`。
- 内容方法 SHA256：`d7f503e9df1cb48c51edd10f033fb37a755f726e420ae71642b5a534e538fd81`。
- 判定：better，accepted=true；一个合成案例，无统计效果结论。
- OpenCode 自报成本 0.153116226；不是提供方账单，正文不据此推算成本优势。

<a id="e20"></a>
## E20 · J2 历史实验与勘误

**证据类型：** 原运行后的辅助审计及单包 pilot 归档；本次读取，未重跑。

旧四包 A/C 原标题包含实验类别线索，辅助判读者已见条件和预期，不能称盲评。ind-08 初稿有 compatible 对照，自动修订丢失绑定，旧汇总已被新增审计勘误；不覆写原记录。

2026-09-13 CodeBuddy pilot 固定单包材料 SHA256 `bf3e5bd1a2de0aa35fe2c2f800c0dc19b0d2facf614f9d35c4c6ce8bdaeabd7f`，CodeBuddy 2.150.0，deepseek-v4.1-flash，effort 未指定，学习关闭。A=`d7995ebe2526fe405b324e3ce841fbb3e9282423`；B=`93d471dbdd7a949cc7456973d2fbcf53bc49ca23`；C=`b74c2365b48de8765026e2e62fdd86f17427b079`。

A/B 取消且无稿；C 超时失败，但保存草稿及 different_scope 对照。A/B/C 均未进入评价、修订或独立 Reviewer。C 墙钟 1803.23 秒，记录授权等待 1512.20 秒；不是模型速度数据。子任务用量未完整分账，整任务 Token 未知；不把 parent 可见输入量作为完整计费量。无法形成有效净收益结论。

<a id="e21"></a>
## E21 · 本文作者的正式源码离线复查

**证据类型：** 2026-09-14 在与发行内容一致的 main 工作树执行；未调用真实模型或搜索服务。

```sh
PYTHONPATH=src python -m pytest tests/test_research_plan.py tests/test_research_handoff.py tests/test_websearch.py tests/test_fact_check_contract.py tests/test_fact_check_phase.py tests/test_fact_check_review.py tests/test_fact_check_orchestration.py tests/test_schedules.py tests/test_learning_integrity.py tests/test_release.py -q
```

结果：`96 passed in 9.04s`，见 [offline-tests.txt](offline-tests.txt)。测试使用夹具与受控模拟；这是本文作者实际执行，区别于 E35 引用的发行团队 CI。原预发布稿 24 项检查保留在私有历史副本，不与本次数量累计。

<a id="e22"></a>
## E22 · 桌面 owner-pipe 与停止顺序

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

桌面专用管道 EOF 触发所属服务停止；先等待本地修改，再取消外部请求。独立 CLI/Web 服务不采用桌面 owner 语义。本次文稿更新未重跑原生强杀。

| 文件 | 符号与固定提交链接 |
|---|---|
| `desktop/electron/service.cjs` | [`async start` · 66](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/desktop/electron/service.cjs#L66) |
| `src/briefloop/server.py` | [`def _watch_desktop_owner` · 88](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/server.py#L88) |
| `src/briefloop/server.py` | [`_active_posts > server._active_connector_posts` · 69](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/server.py#L69) |

<a id="e23"></a>
## E23 · 运行时协议与目录

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

目录不是兼容性证明；各宿主的完整执行、取消和受限审阅能力分别判断。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/harness.py` | [`class ` · 14](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/harness.py#L14) |
| `src/briefloop/opencode_harness.py` | [`class OpencodeHarness` · 93](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/opencode_harness.py#L93) |
| `runtime-bridge/main.ts` | [`network_control` · 75](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/runtime-bridge/main.ts#L75) |
| `runtime-bridge/catalog.json` | [`"id"` · 3](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/runtime-bridge/catalog.json#L3) |

<a id="e24"></a>
## E24 · 冲突处理与复核接纳

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

人工响应保留处理意图；独立检查按预期版本接纳，未知或未决问题不自动消失。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/conflicts.py` | [`def create` · 11](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/conflicts.py#L11) |
| `src/briefloop/conflicts.py` | [`def respond` · 55](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/conflicts.py#L55) |
| `src/briefloop/conflicts.py` | [`def accept_check` · 78](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/conflicts.py#L78) |
| `src/briefloop/source_updates.py` | [`def register_snapshot` · 106](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/source_updates.py#L106) |

<a id="e25"></a>
## E25 · OpenCode Reviewer 工具权限

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

全工具 deny、逐文件 read allow、拒绝符号链接；由宿主执行，不等于 OS 级网络隔离。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/opencode_harness.py` | [`def _permission_rules` · 52](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/opencode_harness.py#L52) |

<a id="e26"></a>
## E26 · 审计包投射与离线校验

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

检查 ZIP、哈希、版本与引用关系，不执行包内代码、不证明世界事实或发布者身份。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/audit_bundle.py` | [`def _project_records` · 43](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/audit_bundle.py#L43) |
| `src/briefloop/audit_bundle.py` | [`def generate_bundle` · 173](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/audit_bundle.py#L173) |
| `src/briefloop/audit_bundle.py` | [`def verify_bundle` · 354](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/audit_bundle.py#L354) |

<a id="e27"></a>
## E27 · Word 修订导入

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

基准版本、原件保存和对齐接纳；不承诺任意 Word 原生对象及修订标记都可恢复。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/word_import.py` | [`def import_revision` · 16](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/word_import.py#L16) |
| `src/briefloop/word_import.py` | [`needs_alignment` · 147](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/word_import.py#L147) |

<a id="e28"></a>
## E28 · 反馈来源与本地 scorer 授权

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

人类反馈可直接记录；机器 pattern 需合法来源。scorer trust 是执行授权，指纹不覆盖全部递归依赖，不是 OS 沙箱。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/wikiskill/resources/product/entry-skill/SKILL.md` | [`Human suggestions` · 68](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/wikiskill/resources/product/entry-skill/SKILL.md#L68) |
| `src/wikiskill/product.py` | [`def feedback` · 411](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/wikiskill/product.py#L411) |
| `src/wikiskill/scorer_trust.py` | [`def describe` · 16](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/wikiskill/scorer_trust.py#L16) |
| `src/wikiskill/scorer_trust.py` | [`def require` · 73](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/wikiskill/scorer_trust.py#L73) |

<a id="e29"></a>
## E29 · 明确人类要求与普通优化

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

正式代码已包含 explicit_human_requirement。全部人类要求满足、无 worse 且无 regressions 时可采用 tie；未满足保留 requirements_pending，在既定轮次内处理。该功能来自 50c0ead0 / PR #700；本文能力依据统一绑定正式提交。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/wikiskill/feedback_loop.py` | [`def finish` · 83](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/wikiskill/feedback_loop.py#L83) |
| `src/wikiskill/product.py` | [`requirements_pending` · 140](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/wikiskill/product.py#L140) |
| `src/briefloop/learning.py` | [`explicit_requirement` · 72](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/learning.py#L72) |

<a id="e30"></a>
## E30 · Windows 环境准备进程归属

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

环境准备 Job Object 与工作区服务 owner-pipe 是不同路径。本次未在 Windows 重跑崩溃注入。

| 文件 | 符号与固定提交链接 |
|---|---|
| `desktop/electron/windows-process.cs` | [`KILL_ON_JOB_CLOSE` · 80](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/desktop/electron/windows-process.cs#L80) |
| `desktop/electron/environment.cjs` | [`async function runWindowsOwnedProcess` · 25](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/desktop/electron/environment.cjs#L25) |
| `desktop/electron/service.cjs` | [`async start` · 66](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/desktop/electron/service.cjs#L66) |

<a id="e31"></a>
## E31 · 对照路径的官方资料

**证据类型：** 2026-09-14 读取的官方项目与产品文档；非第三方测评，未运行这些工具的完整比较试验。

- [STORM／Co-STORM 官方 README](https://github.com/stanford-oval/storm)：多视角提问、人机协作知识探索及用户材料检索。不能概括为只拼装一次性长文。
- [GPT‑Researcher 官方 README](https://github.com/assafelovic/gpt-researcher)：本地材料、多个 Agent 的研究路径、PDF／Word／Markdown 导出。不能声称只输出不可编辑 Markdown。
- [OpenAI Deep Research 官方说明](https://help.openai.com/en/articles/10500283-deep-research-faq)：研究计划调整、过程引导、后续迭代及 Word 下载。

本文据此说明功能交集；没有逐项审计上述产品的 blockId、原件哈希、审阅隔离、冲突与交付机制，未核实能力不写作“没有”。第 1.1 节表格是工程职责比较，不是功能缺失矩阵或性能排名。本次正式稿升级再次读取上述官方页面；文档将持续更新，比较范围固定为本节列出的信息。

<a id="e32"></a>
## E32 · 可选事实核查与审阅编排

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

SAFE 式方法重实现，候选与最终判断分离；同一事务保存结果与阶段收束，取消后不接纳迟到结果；snapshot v7 消费候选。实际模型链与 TLS 限制见 E35。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/fact_check.py` | [`def fact_check_prompt` · 47](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/fact_check.py#L47) |
| `src/briefloop/fact_check.py` | [`def check_fact_result` · 157](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/fact_check.py#L157) |
| `src/briefloop/fact_check.py` | [`def submit_result` · 328](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/fact_check.py#L328) |
| `src/briefloop/fact_check.py` | [`def version_fingerprint` · 139](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/fact_check.py#L139) |
| `src/briefloop/research_plan.py` | [`def admit_fact_check` · 407](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/research_plan.py#L407) |
| `src/briefloop/runtime.py` | [`def _wait_fact_check` · 774](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/runtime.py#L774) |
| `src/briefloop/review.py` | [`if snapshot_version>=7:` · 291](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/review.py#L291) |

<a id="e33"></a>
## E33 · 定时计划与配置冻结

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

计划保存时确定核查开关，每次触发独立任务；服务离线不执行、不批量补跑，计划不重叠。

| 文件 | 符号与固定提交链接 |
|---|---|
| `src/briefloop/schedules.py` | [`def validate` · 66](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/schedules.py#L66) |
| `src/briefloop/schedules.py` | [`def fire` · 142](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/schedules.py#L142) |
| `src/briefloop/schedules.py` | [`def skip_offline` · 136](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/schedules.py#L136) |
| `src/briefloop/schedules.py` | [`def tick` · 186](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/src/briefloop/schedules.py#L186) |

<a id="e34"></a>
## E34 · 桌面差分更新

**证据类型：** 本文作者核对正式发行源码；不单独构成运行成功证明。

Mac 缓存按块复用及 Windows NSIS 差分；校验失败或无法有效复用转全量。本次 Mac 实际走全量回退，不给出未经实测的节省比例。

| 文件 | 符号与固定提交链接 |
|---|---|
| `desktop/electron/updater.cjs` | [`differential` · 7](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/desktop/electron/updater.cjs#L7) |
| `docs/定时报告.md` | [`定时` · 1](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/docs/定时报告.md#L1) |
| `docs/桌面安装与更新.md` | [`更新` · 1](https://github.com/Stahl-G/briefloop/blob/847452bfb39e4185e5d91cc537e030c8e4d7da69/docs/桌面安装与更新.md#L1) |

<a id="e35"></a>
## E35 · 正式发行、同版工件与真实验收

**证据类型：** 本文读取公开发行记录；亲自下载小型证据及 wheel 并核对摘要/PyPI 元数据。未重新执行这些原生与模型场景。

- [正式 Release](https://github.com/Stahl-G/briefloop/releases/tag/v0.21.0)：publishedAt=2026-09-13T22:07:03Z，非 draft。
- [freeze.json](https://github.com/Stahl-G/briefloop/releases/download/v0.21.0/freeze.json) 与 [manifest.json](https://github.com/Stahl-G/briefloop/releases/download/v0.21.0/manifest.json)：冻结提交及共享 wheel。
- [渠道检查记录](https://github.com/Stahl-G/briefloop/releases/download/v0.21.0/published-channel-versions-final.json)：公开渠道版本与工件身份。
- [Mac 原生验收](https://github.com/Stahl-G/briefloop/releases/download/v0.21.0/mac-0210-native-acceptance.md)：DMG 提取后的首次准备、另存、WPS 与退出重开；未覆盖更新器原地安装。
- [Windows 验收范围](https://github.com/Stahl-G/briefloop/releases/download/v0.21.0/windows-0210-acceptance-scope.md)：隔离身份升级；正式产品安装、原生窗口及 WPS 未验证。
- [Mac 差分记录](https://github.com/Stahl-G/briefloop/releases/download/v0.21.0/mac-differential-acceptance.json)：实际全量回退和文件哈希。
- [PyPI 同版本元数据](https://pypi.org/pypi/briefloop/0.21.0/json)：wheel 摘要与 GitHub 下载字节一致。

Release 记录真实报告完成初稿、核查、审阅、一次修订与复核，修订 364 字、4 claim_checks、0 findings。Mac 和 Windows 搜索存在 TLS 失败，使用已存原文不能作为成功联网取证证明。发行 CI 为 Python 611 passed/19 skipped、Windows 安装后123、Mac进程树5、前端34 passed/1 skipped；这些是团队记录，不是本文作者重新执行数。

源码 tree 一致性和小型资产校验摘要见 [release-verification.json](release-verification.json)。下载原始材料与私有历史定位不纳入公开报告包。

## 公开范围

本目录只发布技术报告、证据索引、架构图、所引源码哈希、离线检查日志和脱敏后的发行校验摘要。没有复制工作区原件、凭据、私有计划、原始运行会话或本机绝对路径。
