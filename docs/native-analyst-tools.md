# Native Analyst 按需工具

本设计适用于 `briefloop-native` 的 Analyst，且任务选择 `writer_input_v1`。其他角色、旧写稿协议与 Agent CLI 的接口保持原有行为。

## 初稿入口

初始模型请求只包含五个工具：

| 工具 | 用途 |
|---|---|
| `packet_list` | 查找任务包里的文件 |
| `packet_read` | 读取任务要求、写作经验与来源正文，支持批读 |
| `packet_grep` | 定位原文，随后回读完整相关段落 |
| `write_report` | 仅填写 `title` 和 `markdown`，保存第一稿并取得 revision |
| `load_tools` | 按能力组加载后续操作 |

未加载工具的参数定义和详细操作指南不会进入模型请求。模型一开始只看到各组的简短用途。ReaderContract、WikiSkill 写作经验和来源条件仍是写作依据。

## 按需加载

单独调用 `load_tools({"groups":["evidence"]})`，等成功回执后，在下一轮使用新工具。加载和使用新工具不能放在同一批调用里。加载成功后，实际模型请求中的工具列表和提示随之更新。

| 能力组 | 何时加载 | 工具 |
|---|---|---|
| `evidence` | 初稿保存后补证据、检查和提交 | `assemble_evidence`、`update_draft_details`、`check_draft`、`submit_draft` |
| `revise` | 恢复已有稿、按问题局部修改 | `read_draft`、`patch_report_text`、`replace_report_blocks`、三类证据更新；修订任务另含 `save_revision_metadata` |
| `sections` | 长稿需要分章保存与合并 | `write_sections`、`assemble_report` |
| `calculate` | 需要确定计算或图表数据 | `calc`、`prepare_report_data` |
| `pdf` | 需要目视核对来源 PDF | `render_pdf_pages` |

正常顺序是：读取材料 → 保存正文 → 加载证据组 → 装配证据 → 检查 → 提交。检查发现需改正文时再加载修订组。每次更新等回执中的新 revision，再进行下一笔更新；确定性证据校验与正式交付限制继续生效。检查通过或保存成功不等于完成事实核查。

## 状态与边界

- 加载范围限于运行器已注册的工具，不引入任意代码、Shell 或额外文件访问权限。
- 同组重复加载不重复添加；请求含未知组时整次拒绝，不部分加载。
- 成功加载的组保存到会话。取消后继续、上下文压缩、引擎重启会恢复同一集合；工具配置、角色、任务包位置或固定提示改变时回到初始集合。
- 详细工具指南由版本化代码提供，按组加载；不通过用户个人技能目录隐式扩展权限。
- CLI/API 仍可使用完整 `write_report` 参数。精简的是 Native 模型可见参数，底层证据处理共用原实现。

## 验证

`native-engine/engine.test.mjs` 验证实际模型请求中的工具集合、拒绝路径及恢复。`tests/test_native_writer_loading.py` 使用隔离的本机模拟模型服务，经过真实 SDK 和 Python writer 完成正文、证据、局部修订、检查和保存。该测试不使用外部模型，也不能证明质量或速度收益；性能需要另行控制变量比较。
