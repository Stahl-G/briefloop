# 写作输入与富文本装配

`writer_input_v1` 是按写作任务冻结的可选接口，尚未默认启用。它让模型提交 Markdown 正文，由程序转换为编辑器使用的富文档；候选稿、来源约束、检查版本和最终提交仍使用已有路径。

首次写作使用 `write_report(title, markdown)`。长稿可批量 `write_sections`，再 `assemble_report`。普通表格用 Markdown 管道表格，引用用 `[@src_ID]`，图片仅使用已登记的 `briefloop-figure:fig_ID`。系统不接纳原始 HTML 或任意外部图片，不会把无法表达的格式静默丢弃。

仓库自带证据装配：优先用 `write_report` 单独保存正文，取得 revision 后再用 `assemble_evidence(base_revision, ...)` 一次更新多类证据。兼容同次附 `citations`、`number_bindings`、`temporal_claims`；结构有效但证据定位失败时，正文仍保存，回执明确 `evidence_status=not_saved` 和具体错误。使用返回的 revision 修复证据，不重交整稿。请求本身缺少正文或结构非法时仍拒绝。正文结构、原文行号和记录装配由程序完成，不需要宿主临时编写 Python 脚本。

引用传 `source_id` 与逐字 `excerpt`；数字和日期传 `source_excerpt`。`locator` 可省略：摘录在冻结原文中唯一时生成实际行号，跨行生成行范围；重复时必须提供能消除歧义的 `line` 范围或更长摘录，绝不取第一个匹配。数值、单位、主体、期间、日期和证据含义仍由写作者提供。数字的 `report_quote` 必须在规范正文中逐字唯一，`number_text` 必须在该片段中逐字唯一。来源与正文不得模糊匹配。

`assemble_evidence` 一次校验整个证据批次，通过后只保存一个新版本；失败保留原候选和输入记录，不接纳半批证据。每类最多120条。所传证据数组整类替换，未传类别保留；数字/日期摘录自动补入引用定位记录，但不在正文随意插入引用标记。后续单条修正仍可用已有局部更新工具。结构及定位成功不等于语义核验，仍须 `check_draft` 和 `submit_draft`。

正文保存后，引用定位、数字和日期分别通过 `update_citations`、`update_number_bindings`、`update_temporal_claims` 单独更新。读取已保存字段可获得内容身份键 `record_keys`，在列表重新排序时仍指向同一记录；记录内容改变后返回新键。直接在 `records` 中写记录字段；数字记录的 `value` 就是数值，没有外层包装。修改时在记录内附 `record_key`，删除只传 `remove_keys`。缺口、研究记录、已计算指标可通过 `update_draft_details` 独立更新。程序不会替模型决定主体、期间、单位或引用是否支持结论。

已有稿件用 `patch_report_text` 精确改字，或先 `read_draft(field=body)` 获取 `block_keys`，再替换指定连续块。块键与当前版本绑定。图片、高级表格排版等不能通过 Markdown 块替换降级。指定范围之外的富文本保留。修订任务从冻结原稿初始化，无需模型重新抄写。

所有修改都要携带当前版本或章节哈希。同一稿件每轮只发一个写入调用，等待回执中的新 `revision` 后再更新；执行器串行执行不等于自动更新调用参数中的旧版本号。单类局部更新工具的每批 `records` 最多30条；共用装配入口的每类数组最多120条。`check_draft(revision)` 后只能 `submit_draft` 同一最新版本。结构接纳不等于事实核实或正式交付。

兼容宿主调用相同服务：

```sh
briefloop tool --workspace /workspace writer --run RUN_ID \
  --draft-file /workspace/jobs/JOB_ID/analyst/draft.json \
  --operation write_report --title '报告标题' \
  --file /workspace/jobs/JOB_ID/analyst/article.md
```

其他操作通过 `--file` 传任务目录内的 UTF-8 JSON 参数。检查与提交只用 `--revision`。实际目录和任务身份以冻结写作包为准；任意文件不能冒充活跃写作任务。不要直接覆盖 `draft.json`。

正文、证据分别放在 `article.md` 与 `evidence.json` 时，直接使用：

```sh
briefloop tool --workspace /workspace writer --run RUN_ID \
  --draft-file /workspace/jobs/JOB_ID/analyst/draft.json \
  --operation write_report --title '报告标题' \
  --file /workspace/jobs/JOB_ID/analyst/article.md \
  --evidence-file /workspace/jobs/JOB_ID/analyst/evidence.json
```

例如 `evidence.json`（替换为本次真实来源与逐字片段，不复制示例作为事实）：

```json
{
  "number_bindings": [{
    "label": "收入同比", "value": 20, "unit": "%",
    "entity": "示例公司", "period": "2025年",
    "source_id": "src_EXAMPLE",
    "source_excerpt": "2025年收入1200万元，同比增长20%。",
    "report_quote": "收入增长20%", "number_text": "20%"
  }]
}
```

原文行号和对应引用记录自动装配。已有正文时，用 `--operation assemble_evidence --file evidence-update.json`，JSON中附当前 `base_revision`，不需要再次提交正文。CLI 和 Native 进入相同Python服务，不自动发布或启动模型。

新任务可通过内部 job payload 的 `writer_input_protocol: "writer_input_v1"` 选择本接口，已创建写作包继续使用其冻结协议。旧接口保留作为对照，不与新接口同时暴露给同一次写作任务。此配置不是额外产品模式或发布版本。
