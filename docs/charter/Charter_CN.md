# MABW Charter

This charter defines the architecture and operating disciplines for Multi-Agent
Brief Workflow. It is written in Chinese because it originated from the
project's internal architecture rulings. Public capability claims still depend
on implemented code, tests, docs, and the support matrix.

## MABW Architecture Charters

### 1. 聪明的无权，有权的确定，生效的过人，过人的留痕。

LLM / agent 可以理解、建议、拆分、起草，但不能直接生效；真正写状态、推进流程、冻结证据、通过门禁的，必须是确定性控制面。任何影响后续运行的东西都要人类确认，并留下记录。

裁定测试：如果一个提案让 agent 直接写持久状态、推进 stage、冻结证据、通过 gate、批准交付、修改未来 run 会读取的内容，默认越权；必须改为 deterministic CLI / validator / transaction 执行，或变成 human approval 后的 recorded effect。

### 2. 机器能管的，不交给记忆。

schema、validator、gate、transaction、event log 这些机器强制的部分可靠；只写在 prompt、handoff、口头规则里的东西，在真实 run 里迟早会漂移。凡是能被确定性检查捕获的规则，就不应停留在 guidance。

裁定测试：如果一条规则能被 schema 字段、artifact hash、path existence、status transition、gate result、event presence、reader residue pattern 或测试捕获，就必须进入 schema / validator / gate / transaction / test。如果它依赖语义判断，agent 可以提出、解释、归纳，但不能伪装成强制控制面；只能进入 typed finding、candidate、human review 或 approved record。混合规则必须拆开：能检查的进机器，剩余语义判断留在 agent / human 层。

### 3. 同一个字段只许有一个写者。

每个控制面字段必须有唯一权威写入方。Python 写状态、账本、事件、哈希、门禁；LLM 写内容草稿；人类批准偏好和最终交付。多个模块“顺手更新”同一字段，会破坏审计、回滚和归因。

裁定测试：如果两个模块、命令、agent 或 projection 都想写同一个字段，必须指定唯一权威写者；另一个只能读取、请求 transaction、或写入自己的派生产物。任何“顺手补齐”“重新初始化”“顺便同步”同一字段的实现，都应被视为潜在单写者违例。

### 4. 有来源，不等于被支持；能追溯，不等于被证明。

一条来源记录只证明某个 claim 在何时、从何处、经由哪一步进入流程；它不自动证明该来源在语义上支持这个 claim。检索计划、source candidates、模型摘要、搜索摘要只能作为发现线索，不能作为事实证据。证据支持必须按强度、来源层级和新鲜度分开记录；新鲜不等于权威，有链接不等于被证明。重要 claim 只有满足其 claim class、scope、support strength 和 evidence contract 对应的 gate，才能进入 reader-facing delivery；否则必须 downgrade、block 或转 human review。

裁定测试：如果一个 claim 只有链接、检索计划、source candidate、搜索摘要或模型摘要支撑，不得标成 supported。若 claim 的限定词、数字、时间、归因、范围或新鲜度超出 evidence contract 支持强度，必须 downgrade、block 或转 human review；不得用“可追溯”替代“被支持”。

### 5. 冻住的不许改；缺口不许藏。

一件 artifact 一旦被确定性控制面冻结，就不能被静默覆盖。合法变化必须表现为新的 revision、新的 artifact、新的 event，或显式的 supersede / revert / contamination 记录；不能把旧冻结物原地改写成“好像一直如此”。同一字段的唯一写者也不能回头改写已经冻结的历史。缺失 artifact、未审计证据、失败 gate、失败 transaction、被拒 claim、人类决策缺口，必须成为 finding、blocker、contamination、human-review record 或 event；不能被藏成正文 caveat，也不能从叙述里消失。

裁定测试：如果一个实现需要改变 frozen artifact，必须新增 revision / artifact / event / supersede / revert / contamination，而不是原地覆盖。如果一个负向结果三天后不能被 grep、schema query、event log 或 run archive 找到，就不是“记录得不够好”，而是违反本条。

### 6. 冲突按层级，不按聪明。

当用户请求、agent 建议、audience preference、improvement memory、repair plan、gate、schema、contract 彼此冲突时，系统不靠模型解释谁更合理，而靠预先声明的 precedence 决定谁赢。事实契约和确定性 gate 高于风格偏好；本 run 的 repair 高于跨 run 的 taste memory；控制面义务不被 prompt、handoff 或用户临时请求覆盖。简报目标、读者对象、时间窗口、source policy 和 delivery standard 属于 run direction；agent 可以建议调整，但不能在运行中静默改变。方向变化必须成为显式 user decision、config change 或 new run。

裁定测试：如果两个指令或 artifacts 冲突，不让模型临场解释谁更合理；先查 declared precedence。若冲突涉及 brief objective、reader、time window、source policy 或 delivery standard，必须记录为 explicit user decision、config change 或 new run，不得在当前 run 中静默漂移。

### 7. 横切不变量靠结构闭合，不靠逐路径打补丁。

一条横切不变量——run integrity、staleness、freeze/supersede 语义、repair routing、delivery truth、gate authority——只有当受影响状态的每一个写入方和重算方都遵守它时才成立。逐路径打补丁会退化为反复返工：每条重算路径都会静默丢掉不变量，review 沦为枚举机器。这类变更的合并单位是不变量的完整生命周期，不是某个文件或某一层切片。横切事实的权威只存在一条记录里，所有重算路径去读取它；控制文件经由唯一的 fail-closed 共享加载入口读取；操作指令流只有一个事实来源，改动时在同一变更内扫遍全部契约、adapter、指引产出面和字符串断言测试。

裁定测试：动手前先枚举 state × path 矩阵——受影响状态的每个写入方与重算方、每格的期望结果、每行一条测试；若路径无法枚举，先重设计，把事实移入单一权威记录，使枚举不再必要。如果一个实现把横切事实存进 stage metadata 或任何会被状态重算重建的结构，按设计错误处理，不当作“记得保留”的琐事。只有点名的传播型缺口（尚未迁移的 consumer）可以延期；未经校验即被接受的输入、绕过不变量的路径必须在同一变更内闭合。review 中同形 finding 出现第二次，停止逐路径修补，改结构；出现第三次，说明变更高度错了，先重设计再提交修复。

## MABW Operating Disciplines

### Product Spine: 加速不偷问责。

MABW 可以通过复用冻结证据、减少重复推理、改善引导路径、并行非依赖工作来变快；但不能通过减少 ledger、gate、人类确认、event、snapshot、archive 来变快。轻量化只能轻外壳，不能抽脊柱。

### Public Claims Discipline: 不说 artifact 支撑不了的话。

MABW 的公开文档、README、release note、demo、论文草稿和推广帖，不能宣称超过当前 artifacts 能证明的能力。未测量就写 NOT MEASURED；只能追溯就说 traceability；不能把人工核查发现的错误包装成模型自证；失败案例如果影响能力边界，应作为系统证据的一部分公开。

### Data Boundary: 私有事实不为公共机制背书。

MABW 可以从真实工作流中蒸馏模式、失败类型、控制面规则和测试形态，但私有业务事实、客户事实、雇主材料、IR 内容、未公开信息不得进入 repo、fixtures、公开 demo 或未批准的外部 API。公共机制必须能用公开语料或合成材料复现。
