# BriefLoop × OfficeQA：结构化答案实验协议 v1.0

**协议 ID：BL-OQA-SR-v1.0**  
**日期：2026-09-14**  
**状态：设计稿；未执行模型实验，未取得或处理受限答案数据；不构成开跑授权。**

## 0. 决策摘要

这一次不要求生成行业简报，也不制作 Word。原题、原始答案空间与官方评分器保持不变；把 BriefLoop 最后交付的内容换成一个可机器读取的短答案记录，另存可选的证据与计算附件。

研究目标是：**同一个基础模型、相同语料和可用工具、事先规定的资源约束下，BriefLoop 的研究／证据处理／复核修订过程，能否提高可交付短答案的正确率？**

本协议不证明长报告写作质量、Word 美观、正式交付合规性、实时新闻搜索质量或跨期学习净收益。用户此前讨论的“成稿评测”是另一项研究；本协议改变的是本轮输出终点，不追溯更改旧实验的条件或成绩。

核心路线：

```text
原始 OfficeQA 问题 + 获准语料
          ↓
A：原生 Agent + 认真配置的研究指令
B：BriefLoop 真正执行路径 + grounded_qa 输出适配
          ↓
冻结同一种 answer.json（不是长篇 brief）
          ↓
程序只读取 answer，生成唯一 FINAL_ANSWER span
          ↓
独立评分进程调用未修改的官方 reward.score_answer
          ↓
按全部预定题目报告正确率、完成情况、时间与用量
```

**修改输出适配，不修改官方 scorer 的判分规则。**精确答案不需要复杂主观 rubric；材料定位、计算和修订记录用于诊断，不混入官方主分数。

---

## 1. 依据、边界与版本

### 1.1 本方案核对的代码事实

设计参照 BriefLoop `main@d2e197a6e0a214a111b28ca37152a5fdc019c9e6`。这个 SHA 是此次设计阅读基线，不是未来实验执行 SHA。0.21.0 未提交研究工作树不能与其他分支拼接成一个虚构的“已验证版本”。开跑时必须重新固定集成后的提交、依赖、宿主和配置。[R1]

现有 `src/wikiskill/officeqa/` 已提供数据读取、staged／full-corpus retrieval runner、官方评分器副本与评分包装。它们原本主要驱动直接 `codex exec`，不是 BriefLoop 完整报告产品的评测入口。[R2–R4]

当前 `Requirements`／`BriefDraft` 仍围绕报告建模，包含篇幅、内容方法、正文、引用等字段；本方案所称 `grounded_qa_v1` 尚不是仓库已有开关。[R5] 外部请求接口提供 submit/query/read/export 等操作，但其存在不意味着结构化 QA 和全部 Reviewer 接入已经完成。[R6]

### 1.2 官方基准身份

| 基准 | 官方题数 | 语料 | 本协议用途 |
|---|---:|---|---|
| OfficeQA Full | 246 | Treasury Bulletins，697 份文档 | 已暴露题与开发诊断 |
| OfficeQA Pro | 133 | 与 Full 同一语料；全部问题包含在 Full 中 | 次级准确率报告，先核对历史暴露 |
| OfficeQA Pro V2 | 90 | 另一套联邦收支语料，1,435 份文档 | 首选泛化测试候选 |

这些信息来自官方仓库。[S1] 不能把 Full 的训练题与 Pro 当成独立数据集。Hugging Face 中名为 `train` 的载入 split 只是存储名称，不是官方授权的训练／测试划分。

V2 主要提供结构化 JSON 和 PDF；其 provenance 格式及文件映射与旧版不同。V2 曾重编号 UID，因此 case 身份必须同时包含 **dataset revision + UID + question hash**，不能只存一个数字 ID。[S3–S4]

### 1.3 官方 scorer 固定值

```text
repository: databricks/officeqa
commit: 7b9a3c154ef9fb40215bb67934afc43e6799de16
file: reward.py
Git blob SHA-1: 45a22db4771f20105bdc9340df82e3c405ec84d0
primary tolerance: 0.0
```

该版本允许传入直接答案，也会从 `<FINAL_ANSWER>…</FINAL_ANSWER>` 提取最后一个 span。之后执行直接答案形态检查与官方比较函数；形态检查包括单行、250 字符上限和与参考答案有关的格式规则。[S2]

**所以模型不要输出 XML；由程序生成恰好一个标签对。**这同时兼容当前 BriefLoop `score_stdout` 对标签的使用方式，并防止评测从长文中误抽多个数。[R4]

仓库中的旧 vendored reward.py 必须先核对内容；相同函数名不代表同一个 scorer。执行配置还需记录实际文件的 SHA-256。上面的 Git blob 不是 SHA-256，不能混称。

---

## 2. 研究问题与可宣称范围

### 主问题 H1

在固定实验条件下，B 的最终官方答案正确率是否高于 A？

- 主指标是原版 scorer 的 0/1 输出，不是 BriefLoop 内部评分或 gate 通过率。
- 结论对象是“此次模型＋宿主＋BriefLoop QA 适配＋输入条件”的组合，不能外推到所有模型、所有报告。
- 若不同组使用不同语料表示、模型或隐藏工具，实验只能视为系统组合比较，不能声称只测出了编排收益。

### 次问题 H2

BriefLoop 初始提交到最终提交之间，有多少错→对、对→错、答案不变和失去可用答案的情形？

这属于过程诊断。**在同一运行中观察到修订后变好，不能单独证明 Reviewer 的因果贡献**：它也包含额外计算和时间。需要因果分析时，再增加事先注册的无独立 Reviewer／等预算自检消融，不从最终错误题中挑选子集。

### 次问题 H3

相对 A，B 的正确率变化付出了多少总耗时、模型调用、Token 和估算成本？

如果宿主无法完整提供子任务用量，就报告 `usage_complete=false`；不能用主会话 Token 代表总计算，不能宣称等 Token 或更便宜。

### 本轮不测

长文可读性、排版、Word 往返、长期用户返工、学习净收益、操作系统生命周期、定时任务准时性。它们仍可在自己的测试中验证，但不进入本协议主结果。

---

## 3. 输出设计：三类对象，分别拥有

### 3.1 模型只提交 `answer.json`

```json
{
  "schema_version": "officeqa.answer.v1",
  "status": "answered",
  "answer": "12.50"
}
```

这是合成格式示例，不是任何 OfficeQA 题目的 gold。

必要规则：

1. `answer` 始终是字符串；数字、日期、文本、列表均保持题目要求的直接形式。数值不用 JSON number，避免 `12.50` 被程序变成 `12.5`，也避免巨大整数精度损失。
2. 只回答原题，不附“答案是”、解释、引用序号、置信度、替代答案或 Markdown。
3. 列表按原题顺序写为字符串，如 `"[North, 0.866]"`；不要由评分器对数组重新排序。
4. 单行，去掉首尾空白后最多 250 字符；不允许嵌入 `FINAL_ANSWER` 标签。答案的单位和取整遵守题目，不从 gold 反推。
5. 不能确定时明确提交 `status="abstained", answer=null`，计分为 0；不是“审慎得分”。有最佳答案但仍有局限时可以作答并在附件记录局限，不强迫用 abstention 隐藏错误。
6. 解析器拒绝重复 JSON key、多个 JSON 对象、非法编码和 NaN；不能从 Markdown、聊天记录或计算附件里猜一个答案。
7. 额外辅助字段不会传给 scorer，记诊断警告即可；不因无关元数据或空证据数组而把明确的答案改写。

提供的 `answer.schema.json` 是输出说明；最终评分核心还以参考适配器的 gold-blind 校验规则为准。模型不分配 case_id／run_id／版本号，不写 score、ready、approved、官方 scorer 结论或自称已经核验的哈希。

### 3.2 可选证据附件 `evidence_draft.json`

```json
{
  "schema_version": "officeqa.evidence.v1",
  "evidence": [
    {
      "source_id": "synthetic_table_01",
      "locator": {"kind": "line_range", "start": 2, "end": 3},
      "excerpt": "Year A: 100 million.\nYear B: 112.5 million."
    }
  ],
  "calculations": [
    {
      "expression": "112.5 - 100",
      "inputs": [
        {"name": "Year A", "value": "100", "evidence_index": 0},
        {"name": "Year B", "value": "112.5", "evidence_index": 0}
      ],
      "result": "12.50"
    }
  ],
  "limitations": []
}
```

同样是合成例子。它记录公开可核对的原文、操作数与计算关系，**不要求保存或公开模型的隐藏思维链**。

- source_id 由资料工具返回；只能使用允许语料中存在的 ID。
- 文本行号为 1-based；PDF/JSON 的 `page_index` 为 0-based。页索引不等于纸面印刷页码。
- 使用结构化元素时同时记录 page_index 与 element_index。真实映射在 query-blind 的语料构建阶段生成，不能用 gold provenance 来填。
- 验证程序记录 excerpt 是否能定位、范围是否存在。定位匹配不等于来源语义支持答案。
- 计算表达式是文本，不由 scorer 以 `eval` 执行。必要的确定计算另用预注册白名单工具验证。
- 正式任务不硬性要求长解释或填写很多中间项。证据附件为空时保留官方答案得分，另计证据缺失。

官方 scorer **永不读取该附件**。不能因为里面碰巧包含 gold，就把错误 `answer` 判对。

### 3.3 Harness 保存 `episode_record.json`

由程序生成：协议、数据 revision、case key、随机次序、组别、真实模型／宿主身份、开始／截止时间、预算、工具调用、费用完整性、提交序列、实际 run/job/version_id，以及所选答案文件的原始字节 SHA-256。

证据附件与答案提交同时绑定。新答案不能继续使用只适用于旧答案的审阅身份。即使本轮不输出 Word，也保留“正在核查哪次提交”的清晰边界。

官方评分结果放在 evaluator-only 目录，不回写给当次 solver、Reviewer 或学习任务。

---

## 4. 评分投影：薄包装，不建立新评分器

投影必须在读 gold 之前完成：

```python
# 伪代码；完整独立参考实现在 score_answer_record.py。
projection = project_answer(answer_record)  # 不能访问 gold
if projection.status == "abstained":
    official_score = 0.0
else:
    submission_text = "<FINAL_ANSWER>" + projection.answer + "</FINAL_ANSWER>"
    official_score = official_reward.score_answer(
        ground_truth=gold,
        predicted=submission_text,
        tolerance=0.0,
    )
```

禁止：另请模型提取“真正答案”；根据 gold 选单位／精度／列表顺序；把解释中的正确数补进答案；搜寻所有候选取最高分；调大 tolerance 救某组；只对 BriefLoop 做格式修复。

允许：首尾空白清理；固定标签包装；官方 scorer 本身规定的格式处理；两组在预算内拥有相同次数、无 gold 的结构错误反馈。

格式修复最多一次（整次 episode 共用），需要真实 Agent 重交并记录耗时／Token；不得由控制程序代答。预注册后不可依据测试成绩提高修复次数。

**错误分层**：

- JSON／核心字段错误、明确弃答或到期无已接纳答案：主指标为 0，原因单列。
- scorer 文件哈希不符、gold 数据损坏、评测程序异常：评测基础设施错误，暂停该批评分并修复评测；不能伪装成 solver 答错，也不能静默排除不利样本。
- 证据定位失败、附加计算缺失、内部审阅失败：官方答案分数不变，另列诊断。
- 发现答案泄漏或中途改协议：实验有效性问题，不是一般失败；停止并标记污染，重建干净实验时保留原记录。

主评分固定 tolerance=0.0。需要宽容度分析时可预先附加全题统一的 0.01 敏感性分析，主表不替换为最佳 tolerance。发现官方评分疑似误判时保留原分数，另外发布 disputed_case 清单和人工解释，不给某一组偷偷修分。

---

## 5. 实验组与产品接入

### 5.1 主实验只用两组

| 组 | 配置 | 不允许的削弱 |
|---|---|---|
| A：Native control | 同一宿主／模型、原题、共享语料与工具；认真配置的资料检索、读表和自检指令 | 不禁止本来可用的代码计算、验证或原生子任务；不是无工具一次生成 |
| B：BriefLoop QA | 现有研究规划、来源接纳、证据对照、版本保存与支持的独立审阅／一次修订；输出 answer.json | 不能改用另一个更强 Reviewer；不能通过测试专用脚本替代真实研究 |

两组都可以在预算内自检，都按同一答案契约提交。B 的额外机制、角色和上下文调用属于处理条件，要公开并计量，不能声称额外思考是免费。

共用基础研究指令可以复用旧 OfficeQA 中按文档年代、主题、指标和表头定位的通用规范，不能复用包含测试题答案、正确文件列表或已暴露题特征的技能。

只测试一个预先选择的真实可用模型组合。每个角色使用同一模型及推理档；宿主与模型选择写入配置后才开跑。不能从“同名模型”推定 provider 或版本相同，也不能在观察到成绩后为某组单独换模型。

### 5.2 哪些 BriefLoop 功能保留

保留题目规划、原文读取／图表查看、实际计算工具、来源登记、需要时的写前对照、冻结答案的独立核查、最多一次纠错、状态及结果记录。

本轮取消完整摘要／正文写作、字数目标、商业行动建议、图文排版、Word、正式发布、邮件和 UI 美观评分。自动学习、定时报告、企业背景、历史对话与不相关 MCP 连接均关闭。原始记录仍可浏览，不需要创造“正式交付”状态才能评分。

**不要关闭证据与复核过程，只留下一个直接问答调用，却把它叫完整 BriefLoop。**也不要为了复用类名，强制模型花 Token 写一份无用的长 brief。

### 5.3 最小改造方案（建议，尚未实现）

增加显式 opt-in 的 `result_format=grounded_qa_v1`，默认仍是现有报告。该模式贯穿要求快照、生成／修订产物接纳和 Reviewer 输入，不成为全局替代。

优先复用现有 run/jobs/version/Review：把答案 JSON 作为该版本的结构化内容保存；为现有依赖正文的页面或 Reviewer 生成一份**确定性的最短审阅投影**，仅包含问题、答案与证据入口。它可以内部复用 BriefDraft，但不是让 LLM 再写 brief。原始 answer.json 始终是唯一评分值，投影必须机械一致，不能再抽取或改写答案。

若现有模型会 prune unknown 字段，不能把 QA 结果塞进未知字段后假设它会保存。需在受控模型中新增明确字段或受版本绑定的工件引用。审阅指纹包含答案与相关附件哈希，修订必须提交新答案版本；旧核查不能自动继承。

生成和内部评价的内容方法改为“回答该问题、识别证据不足或冲突、核对单位和计算”，去掉报告篇幅、文采、摘要和完整章节要求。方法差异必须在协议中列出；结果对外称 **BriefLoop QA profile**，不称整个桌面报告产品都已被验证。

### 5.4 独立 Reviewer 是能力门，而不是一个标签

先核实所选宿主在冻结版本中能执行所需的受限审阅。缺少能力时：更换两组共同宿主并重新冻结，或预先把 B 命名为“BriefLoop research-only”。不能运行时退回主会话自评却继续宣称独立复核。

Reviewer 不得看到官方 gold／评分结果，也不得读取其他实验组答案。若本次测的是对已发现材料的复核，Reviewer 只读同一冻结包；补搜由作者修订阶段按预算执行，需记录研究与审阅的职责边界。

---

## 6. 语料、联网与泄漏控制

### 6.1 主条件：共享解析＋全语料，PDF 按需可见

A/B 使用完全相同的官方解析结果及确定性转换，保留表格、页映射和脚注。源 PDF 可由相同工具按页查看，视觉能力和权限对齐。全语料索引必须 query-blind，构建时不读题目的 `source_files` 或 `source_docs`。

旧 OfficeQA TXT 与 V2 JSON 分别做适配，不把 V2 JSON 直接 repr 成一坨文本。不能从题目 gold provenance 中给全语料模式补正确年份、正确文件或页面提示。[S1, S3]

V2 1,435 份文件全部进入检索宇宙；只纳入全部测试题引用的 249 份，仍然使用了 gold 关联信息，不算完整检索。原始解析／索引成本单列；A/B 共享材料实验不证明 BriefLoop 自己的 PDF 解析更好。

不把全部文档提前塞入每题上下文。用同一 read-only corpus 工具提供搜索、片段读取、按页查看；B 在实际使用后接纳来源。若当前产品不能从只读本地全集开始，需加真实、受控的资料入口，不能用 `allow_web=true`、假来源或“授权已验证”布尔值绕过前置要求。

可优先复用现有逐报告 MCP 接纳路径，做本地只读语料适配；A 获得同一 search/read/render 能力。它不是远程付费数据服务。选择此路线前做一个无答案的完整语料可见性探针，确认权限、分页、全部文档可寻以及来源接纳都真实有效。

### 6.2 Oracle 只用于诊断，另表报告

开发时可提供正确文档做 `oracle_documents`，或正确页面做 `oracle_pages`。不能与 full-corpus 混算；只能说明在已经找到材料时能否读对与算对。

### 6.3 不能默认整套题断网

官方说明部分题需要额外宏观资料等网页补充。[S5] 主配置允许两组通过同一受控搜索与读取入口补证，记录全部请求，禁止浏览 benchmark 答案键、公开解题文章和评测输出。

若要测纯离线行为，应另建已确认语料自足的子集，或准备不依赖 gold 答案的冻结补充资料包，并明确标注这是改动条件，不是直接复现官方网页可用条件。不得在看成绩以后按是否答对决定哪些题“需要网页”而移出分母。

### 6.4 数据区必须真正隔离

```text
solver runtime：仅当前题目、获准语料／工具、自己的 episode 工作区
internal reviewer：当前冻结答案和允许证据／历史，不见 gold 和其他组
external scorer：gold、provenance、固定 scorer、所有冻结结果
```

不能把完整 CSV 放在 Agent 可 grep 的仓库父目录、缓存或其他工作区。Hugging Face 下载凭据只交给数据准备进程；solver 运行时没有 HF_TOKEN，也没有完整答案 CSV 的文件权限。仅通过提示词说“不要读答案”不算隔离。

从 gated payload 导出的 `question_only.jsonl` 不包含 answer、source_files、source_docs、difficulty 派生提示或每题正确文件数。case key 的题目哈希可以公开；不发布容易被枚举还原的逐题 gold 哈希。

公开数据／输出时遵守数据门控与许可证。代码与方法可公开；不在本实验包分发 gold、受限题干或受限来源复制品。生成答案与引用也需在发布前检查，而不是因为“模型生成”就自动认为可公开。

---

## 7. 数据划分与既往暴露

先整理 exposure_ledger：每个数据 revision/UID/question hash 是否被作者、开发 Agent、Skill 演化、人工审查或旧实验使用过；未知记 unknown，不记 unseen。

建议执行顺序：

1. **合成接口测试**：本包样例，不触碰 benchmark gold。
2. **6 题开发 pilot × 两组 = 12 次 episode**：来自明确已暴露的旧 OfficeQA 或有意划入开发集的题。可用 oracle 文档定位故障，结果只作开发报告。
3. **主测试**：优先 Pro V2 的未用于调优题。候选总集 90 题；已被公开案例讨论或本项目实际使用的题按清单从 clean generalization 统计中分开。只有当暴露记录支持时才把全部 90 题叫干净测试。
4. 可以仍报告全 90 题的描述性成绩，但必须同时报告未暴露子集及分母，不能隐藏已用题。历史 Pro 的成绩也独立列出，不与 V2 直接平均。

不要重新打散已用过的旧 OfficeQA 再宣布一个新 test。不要把已经讨论并用于调试的 Medicare 案例放进“未见案例”。数据难度分层可以用于事先抽样，但不得将 gold 页码／文件数泄漏给 solver。

一次正式试验中，不做在线学习，不让第 1 题反馈进入第 2 题。确需评估 WikiSkill 时另立扩展：仅开发题形成技能，冻结技能后测试新题；官方测试分数不能反馈给 proposer，再继续在同一题集宣称独立测试。

---

## 8. 资源、运行次序与终点

### 8.1 资源建议（pilot 后一次性冻结）

起始建议：单 episode 墙钟上限 1,800 秒；同时活跃模型调用上限 4；B 最多一次自动修订；A 可在其剩余预算内自检。搜索预算和已接纳正文预算，两组使用相同工具实现和相同数值，不能给每个子 Agent 再发一份预算。

主作者、Scout、内部 Evaluator、Reviewer、修订全部计入该次任务。索引的离线前置成本另表列出。不将排队、授权等待或冷启动从用户延迟里隐去；同时细分 active_execution、permission_wait、queue_wait、startup 便于归因。

只有当所有调用都可观测且可限制时，才设并宣称“总 Token 硬上限”。否则准确称为**同模型、同工具、同墙钟／并发／搜索约束**比较，Token 和成本作为观察值，不能写“等计算预算”。

账号额度由用户预先授权，协议本身不授权付费。重试如果产生新的模型样本，需继续占本 episode 预算，不另开一个免费的 attempt。找不到或无法确认模型身份时保留未核验／协议偏离，不从榜单名字猜配置。

### 8.2 调度

按题目配对，随机决定 A/B 先后；两组在同一时间窗口交错运行，避免全部 A 跑完后供应商环境变化再跑 B。主 pass 仅一次，不按最高分挑运行。共享只读语料／中立缓存可以，提示、对话、Wiki、工具结果及答案缓存不得跨题／跨组泄漏。

避免同时跑两组争抢同一订阅额度造成系统性排队。数据集索引可共享，但每个 episode 使用独立、干净的工作区和会话；关闭定时任务、自动学习及其他后台模型作业。

### 8.3 明确的答案提交终点

每次提交答案时，由程序接纳、分配顺序号和时间，保存原始字节及 SHA-256。初始提交在首次复核前固定，后续修订另存。执行失败不能静默抹掉已经合法保存的答案。

**主评分使用截止时点之前“最新一份已明确提交并成功接纳的有效答案”。**不是任意文件中的最新修改时间，不是最后一条聊天，也不是事后按 gold 在初稿与修订中二选一。

- 复核失败但初始答案仍有效：可以评分初始答案，同时标记完整工作流未完成。
- 最新合法提交是 abstained：计 0，不回退挑旧的猜测。
- 新草案格式错误尚未接纳：保留错误，并继续使用此前已接纳答案（若有）。这个规则两组一致。
- 到期无已接纳答案：计 0。
- job 完成状态、内部 ready、gate 通过与否不影响官方答案是否计分。不得只统计放行成功的题。

这一终点对应“用户截至期限实际可以取得的答案”。另外单列 end_to_end_complete_rate 和 incomplete_with_answer，避免把保留下来的草稿说成整条核查流程成功。

先冻结所选答案及全部组别的预测，再在隔离评分进程中读取 gold。初稿／修订的诊断分数也在同一冻结以后计算，不返回当次 Agent。

---

## 9. 指标、统计与发布表

### 9.1 主指标

对每个预注册可评分题 i 与实验组 a：

`Y[i,a] = official_score(gold[i], projected_answer[i,a], tolerance=0.0)`

`Accuracy[a] = sum(Y[i,a]) / N`

N 是冻结题目清单，不是答出来的题数；到期无稿、弃答和格式错误保持在分母中。主差异为 `Delta = Accuracy[B] - Accuracy[A]`，报告百分点与正确题数。

没有可接受答案时由 runner 创建 harness-owned 缺失结果并计 0；不伪造模型提交的 answer.json。本包适配器评分的是已经选择和冻结的一个文件，批次分母、缺失记录与选择规则由 runner 实现。

### 9.2 必须同时报告

| 项目 | 含义 |
|---|---|
| valid_answer_rate | 有可用短答案而非无输出／格式错／弃答 |
| end_to_end_complete_rate | 所声明的整条生成与核查流程实际完成 |
| wrong→right / right→wrong | 在统一冻结后比较初始与最终答案 |
| evidence_missing / locator_failure | 诊断证据是否记录、定位可否核对；不是语义支持率 |
| median / P90 wall time | 全任务用户等待时间；另分授权等待 |
| all-role model calls & tokens | 包括全部子任务；不完整明确标识 |
| cost per correct answer | 总已观测成本 / 正确题数；零正确时 N/A |
| protocol_deviations | 模型、语料、权限或预算偏离，不藏在平均值里 |

内部 Reviewer 判通过只能命名为其真实内部状态。QA mode 没有正式交付结果时不编造 `delivery_passed`。若另外研究 selective answering，必须同时报告 coverage 和 conditional error；“全拦住”不能当高可靠性的优势。

### 9.3 不确定性

主 pass 对题目做配对 bootstrap 95% 区间，保存随机种子；必要时对 discordant pairs 给出 exact McNemar 检验。不能把多个字段当独立样本。只有 90 题时应同时给整数题数，不夸大几题造成的百分比变化。

追加重复运行时，题集和次数提前固定；报告每题各次均值或各完整 pass 的成绩，不使用 best-of-k。bootstrap 按题目聚类，不能把同一题跑三次当三道新题。pilot 可按完成度决定能否开正式试验，不可按已看到的测试优势临时停止或增加样本。

### 9.4 结果模板

```text
Dataset: [revision + eligible N + exposure policy]
Input: full corpus / shared parse + PDF / network policy
Model & runtime: [actual identities]

Arm                Correct / N   Accuracy   Valid answer   Complete E2E   Time   Usage complete
Native control     -- / --       --         --             --             --     --
BriefLoop QA       -- / --       --         --             --             --     --

Paired Delta: -- percentage points [95% interval]
B wrong→right: -- ; B right→wrong: --
No-answer / abstention / format-error: -- / -- / --

Not established: report quality, Word formatting, long-term learning benefits.
```

支持改善的表述：“在 X 数据与条件下，B 较 A 多答对 k 题，差异 Δ，区间…，成本…”。区间很宽或跨零时如实称未建立稳定收益。负结果同样发布。故障修复后的新版实验必须有新实验 ID，旧结果保留。

---

## 10. 实施单元与验收

### Q0：评分适配与合成测试

复用本包 adapter/schema；下载并核对固定官方 reward.py；实际执行官方 scorer 的合成等价测试。检查数值、货币、列表、日期、空结果、重复键、标签注入、把正确数字藏进附件等情况。

已在本包完成的只有 20 项离线适配器测试，使用合成 spy/stub scorer；它们证明抽取和隔离契约，不是官方 scorer 回归。**完整上游评分器与受限数据的实际联调仍待实施。**

### Q1：真实 BriefLoop QA 输出模式

新增 opt-in 契约与最短审阅投影；默认报告路径不变。核对保存、初始／修订绑定、Reader/Reviewer 使用的准确答案、退出失败后已有答案仍可读。不新增 Excel/Word 输出要求。

### Q2：数据与 episode runner

复用数据读取与模型身份记录思路，新增 V2 映射；真实挂载全文语料，不用 gold 筛资料。固定 config、输入清单、暴露记录与状态日志。runner 两组共用工具和资源约束；B 驱动真实 run/job/review 接口，不直接写 SQLite 模拟完成。

### Q3：开发试跑与冻结

运行预先选择的 6 题开发 pilot；修复只影响基础完成的接入问题。确认无需人工代写、不泄漏答案、scorer 不参与解题、所有角色成本能够正确记录。然后固定执行 SHA、数据 revision、scorer hash、问题清单、模型和预算。

### Q4：正式执行与独立评分

按冻结清单执行，先封存预测再评分。报告所有预定题的状态，公开配置与可合法分发的证据；受限数据由授权复现者自行取得。QA 主实验完结后再决定是否新增研究深度或学习消融，不把所有问题堆进第一批。

### 推荐的代码落点（均为建议）

```text
experiments/officeqa_structured/
  protocol.md
  config.json
  prepare_dataset.py
  corpus_adapter.py
  run_episodes.py
  score_predictions.py
  analyze_results.py
  schemas/

src/briefloop/answer_result.py          # 新增最小类型和机械投影
src/briefloop/models.py                # 显式结果格式，不丢失 QA 字段
src/briefloop/runtime.py               # 现有任务分派与 QA 内容方法
src/briefloop/review.py                # 冻结答案及证据消费
```

不要搬回旧 mandatory Claim Ledger/Editor 审批链，也不要建立第二个业务 Store。不要为了该 benchmark 硬编码题目、年份、文件名或答案关键词。通用错误修复可以合并产品；OfficeQA runner、gold 和分数计算留在实验侧。

---

## 11. 给实验 Agent 的最终检查

开跑前必须回答：

1. 哪个准确提交和产品模式被测？0.21.0 功能是否真的已经集成？
2. 哪些题曾被看过或优化过，剩余测试分母是多少？
3. A/B 是否看到相同完整语料与搜索能力？有没有正确文件映射泄漏？
4. 哪个官方 scorer 文件在运行？答案格式是否只做确定性投影？
5. 断网是否让某些问题缺必要补充资料？有没有清楚标记变更条件？
6. 真正的独立 Reviewer 是否可用，还是只能自检？
7. 所有模型调用、自动修订、等待和失败是否计入？
8. 哪个已提交版本在截止时被选中，是否在读取 gold 前已固定？
9. 弃答、格式错、失败和被内部拦住的答案是否全部保持在分母？
10. 谁授权执行这批有成本的任务？配置是否已无 null/TODO？

缺任一关键条件时，先停留在开发检查，不把“不完整实验”改名为正式结果。

---

## 12. 来源索引

以下是本方案实际核对的外部事实依据；其余实验参数、输出结构和实施路径为本方案建议，不冒充官方规定。

- **[R1]** BriefLoop `d2e197a6e0a214a111b28ca37152a5fdc019c9e6`：https://github.com/Stahl-G/briefloop/tree/d2e197a6e0a214a111b28ca37152a5fdc019c9e6
- **[R2]** 旧 OfficeQA 数据适配：`src/wikiskill/officeqa/dataset.py`。
- **[R3]** 旧 staged/retrieval 执行：`src/wikiskill/officeqa/rollout.py`、`retrieval.py`。完整路径均相对 R1。
- **[R4]** 现有评分包装：`src/wikiskill/officeqa/scoring.py`，本轮通过连接器读取，要求 FINAL_ANSWER 格式。
- **[R5]** 当前输入与报告模型：`src/briefloop/models.py`，本轮通过连接器读取。
- **[R6]** 当前外部请求边界：`src/briefloop/external_requests.py`；研究与任务状态见用户提供的技术报告正文及对应代码。
- **[S1]** Databricks OfficeQA README，固定 commit `7b9a3c154ef9fb40215bb67934afc43e6799de16`：https://github.com/databricks/officeqa/blob/7b9a3c154ef9fb40215bb67934afc43e6799de16/README.md
- **[S2]** 同一提交的官方 `reward.py`：https://github.com/databricks/officeqa/blob/7b9a3c154ef9fb40215bb67934afc43e6799de16/reward.py
- **[S3]** Pro V2 数据卡（读取日 2026-09-14）：https://huggingface.co/datasets/databricks/officeqa-pro-v2
- **[S4]** Pro V2 UID 变更记录：https://huggingface.co/datasets/databricks/officeqa-pro-v2/discussions/1
- **[S5]** Databricks 官方 Pro V2 介绍，外部信息／视觉条件与泛化定位：https://www.databricks.com/blog/introducing-officeqa-pro-v2-new-benchmark-enterprise-grounded-reasoning

本包不包含官方 reward.py、受限数据或任何 benchmark gold。参考适配器是新编写的、未接入 BriefLoop 的独立组件；生产接入和正式数据评测尚未实施。
