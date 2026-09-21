# 写作输入与富文本装配

`writer_input_v1` 是按写作任务冻结的可选接口，尚未默认启用。它让模型提交 Markdown 正文，由程序转换为编辑器使用的富文档；候选稿、来源约束、检查版本和最终提交仍使用已有路径。

首次写作使用 `write_report(title, markdown)`。长稿可批量 `write_sections`，再 `assemble_report`。普通表格用 Markdown 管道表格，引用用 `[@src_ID]`，图片仅使用已登记的 `briefloop-figure:fig_ID`。系统不接纳原始 HTML 或任意外部图片，不会把无法表达的格式静默丢弃。

正文保存后，引用定位、数字和日期分别通过 `update_citations`、`update_number_bindings`、`update_temporal_claims` 单独更新。读取已保存字段可获得内容身份键 `record_keys`，在列表重新排序时仍指向同一记录；记录内容改变后返回新键。直接在 `records` 中写记录字段；数字记录的 `value` 就是数值，没有外层包装。修改时在记录内附 `record_key`，删除只传 `remove_keys`。缺口、研究记录、已计算指标可通过 `update_draft_details` 独立更新。程序不会替模型决定主体、期间、单位或引用是否支持结论。

已有稿件用 `patch_report_text` 精确改字，或先 `read_draft(field=body)` 获取 `block_keys`，再替换指定连续块。块键与当前版本绑定。图片、高级表格排版等不能通过 Markdown 块替换降级。指定范围之外的富文本保留。修订任务从冻结原稿初始化，无需模型重新抄写。

所有修改都要携带当前版本或章节哈希。同一稿件每轮只发一个写入调用，等待回执中的新 `revision` 后再更新下一类证据；执行器串行执行不等于自动更新调用参数中的旧版本号。每批证据最多 30 条，超过时分批提交，每批接续上一批回执。`check_draft(revision)` 后只能 `submit_draft` 同一最新版本。结构接纳不等于事实核实或正式交付。

兼容宿主调用相同服务：

```sh
briefloop tool --workspace /workspace writer --run RUN_ID \
  --draft-file /workspace/jobs/JOB_ID/analyst/draft.json \
  --operation write_report --title '报告标题' \
  --file /workspace/jobs/JOB_ID/analyst/article.md
```

其他操作通过 `--file` 传任务目录内的 UTF-8 JSON 参数。检查与提交只用 `--revision`。实际目录和任务身份以冻结写作包为准；任意文件不能冒充活跃写作任务。不要直接覆盖 `draft.json`。

新任务可通过内部 job payload 的 `writer_input_protocol: "writer_input_v1"` 选择本接口，已创建写作包继续使用其冻结协议。旧接口保留作为对照，不与新接口同时暴露给同一次写作任务。此配置不是额外产品模式或发布版本。
