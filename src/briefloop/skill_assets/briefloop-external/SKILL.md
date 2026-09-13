---
name: briefloop-external
description: Use an existing local BriefLoop workspace from WorkBuddy or another shell-capable agent to submit report tasks, inspect saved sources and drafts, revise a specific version, and download Word files.
---

# BriefLoop 外部报告入口

适用于用户明确选择的本地 BriefLoop 工作区。通过已运行的 BriefLoop 完成研究和报告生产；保留其模型、企业背景选择、模板与独立审阅要求。

## 连接与提交

使用已安装的 `briefloop`，或同一 Python 环境中的 `python -m briefloop`。先执行：

```sh
briefloop external --workspace "/absolute/workspace" discover
```

该命令不会创建目录、数据库或启动服务。`ready=false` 时将实际原因告诉用户；需要先在 BriefLoop 打开该工作区。不要改连其他工作区。无需读取、复制或展示 API Key。

将请求保存为 UTF-8 JSON 文件，然后执行：

```sh
briefloop external --workspace "/absolute/workspace" request --file request.json
```

可用请求：

| action | 字段 | 返回内容 |
|---|---|---|
| inspect | 无 | 最近来源与稿件 ID；列表有数量上限 |
| source | source_id | 保存的来源正文与出处 |
| submit | request_id、requirements、source_ids | job_id、run_id |
| query | job_id | 状态、错误、对应版本和文件是否可用 |
| read | version_id | 保存的 Markdown 与 editor_document |
| revise | request_id、base_version、editor_document | 新 version_id |
| export | request_id、version_id | Word 导出 job_id |

先 `inspect` / `source` 确认所选材料，再提交用户要求。例如：

```json
{"action":"submit","request_id":"report-unique-id","source_ids":["src_actual_id"],"requirements":{"title":"本期简报","objective":"概括已上传材料的变化及影响","allow_web":false,"writing_mode":"general","target_words":500,"max_words":700}}
```

ID 必须来自实际响应。企业内报告使用 `writing_mode: "internal_report"`；若系统要求先选择企业背景维护方式，应在 BriefLoop 完成选择，不能改成 general 绕过。`requirements` 使用 BriefLoop 既有需求字段；模型沿用工作区设置，接口不暗改配置。

## 重试与进度

每次新的写操作生成一个唯一 `request_id`，保存完整请求和返回 ID。响应丢失时，用**原 ID、原内容**重试；`replayed=true` 表示返回既有结果。同一 ID 改内容会报冲突。不要靠换 ID 恢复未知结果，否则会新建任务。

提交后向用户报告任务已接收，并用 `query` 查询原 job；无需一次调用等完整报告。查询间隔适当退避，例如 5 秒到 30 秒。失败、取消、中断是明确状态，报告实际错误及已保存稿件，不自动重建任务。`latest_version_id` 是该报告的最新稿件，可能来自后续修订；导出必须明确选定版本。

## 改稿与 Word

先 `read` 精确版本，保留完整 `editor_document` 的图、表、引用和格式，仅修改用户要求的部分；用该版本作 `base_version`。发生“已有更新”冲突时重新读取并合并，不能覆盖新稿或丢掉未修改节点。保存成功后再次读取新版本。

`export` 只排队生成指定版本的**工作稿**，不代表正式交付审核通过。查询导出 job 至 `artifact_available=true`，再下载：

```sh
briefloop external --workspace "/absolute/workspace" download --job job_actual_id --output "/existing/output/report.docx"
```

客户端校验 SHA256，已存在的相同文件直接复用，不覆盖不同文件。导出文件缺失时原请求不会自动重建；用户决定重新导出后再用新 request_id。交付给用户版本 ID、实际文件路径和剩余问题；不要把排队或生成文件写成已通过独立审阅。
