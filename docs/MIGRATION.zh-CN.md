# 迁移说明

本页说明公开架构如何从旧 Python-pipeline 叙事迁移到当前 司乐师-first 叙事。

| 旧叙事 | 当前叙事 |
|---|---|
| Python 拥有完整 brief workflow | Runtime main agent 协调 delegated subagents |
| `prepare` 是主要生成路径 | `run` 是 运行交接单 launcher |
| Python class 充当 workflow agent | 外部 runtime role 充当 subagent |
| 只靠 prompt 控制流程 | 通过 契约-governed handoff 和 validation 控制 |
| 质量只是后期编辑问题 | 质量进入 evaluation 和 feedback loops |
| private feedback 混入 context | feedback 被治理，并与 evidence 分离 |

## 迁移规则

- 当前切换是 fresh-only。新的 Codex run 只以 SQLite `briefloop.db` 为权威；
  JSON/JSONL control files 只是投影或退役遗留物。
- JSON-only workspace 不受支持，也没有 importer、静默迁移、dual read/write、
  compatibility mode 或 fallback。
- Schema 13 为 fresh current-schema workspace 增加正常 successor run 与不可变的
  approved-guidance snapshot relation。development schema 变化后请新建 workspace；
  产品内不提供旧 development workspace 升级路径。
- Schema 18 为 `solar-stock-periodic` 增加 fresh-only 的冻结多任务搜索计划、Tavily
  acquisition bundle、逐任务结果和行情快照边界。Schema-17 workspace 不迁移、不
  dual-read，也不原地升级；要运行 Solar Stock Periodic，必须新建 schema-18
  workspace。缺少行情快照时保持缺失，不用伪造价格或估值倍数填空。
- Schema 19 用严格的 `briefloop.market_data_snapshot.v2` 权威替换 fresh-only
  行情 v1 表，冻结 workbook identity、复权历史、公司行动、汇率、估值、事件、
  缺口与冲突。Schema-18 workspace 不升级、不 dual-read；读取 profile-bound XLSX
  前必须新建 schema-19 workspace。
- legacy Improvement JSON/JSONL 与对应 mutator、fast-rerun/080 命令均已退役。
  可选 Semantic Assessment Report 只保留非阻断的 schema/reference validation；
  producer、status/proposal projection 与 adjudication writer 已退役。
- 实验性 post-final review 通过 SQLite Receipt 记录显式选择的 assessment result、
  Human accept/reject/defer、人工编辑 guidance draft 和独立 approval/status。
  Human 可以另行调用 `briefloop runtime successor-start`，提供新 run ID、严格
  `RunDirection`，并用 `--include-approved-guidance` 明确选择复用。一个 Core
  事务原子创建同 workspace 的正常 successor，并只为 Analyst/Editor 冻结完整、
  兼容且 active-approved 的 guidance 集合。
- 当前 direction 与 evidence 始终优先；guidance 不是 fact、source、Claim Ledger
  input、Gate rule、repair command、finalize/delivery authority 或 Core policy。
  效用 NOT MEASURED，也不构成自动学习。
- 不要恢复 Python full-pipeline 作为标准生成路径。
- 不要把 roadmap 目标当成已实现模块。
- validator 或 audit check 应该执行的硬约束，不要塞进 user notes。
- runtime-specific adapter 不应改变公开 artifact expectations。
