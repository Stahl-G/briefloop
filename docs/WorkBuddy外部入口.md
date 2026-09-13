# WorkBuddy 与本地 Agent 外部入口

外部 Agent 可以使用 BriefLoop 的现有工作区提交报告、查看进度、读取材料和稿件、保存修订、下载指定版本的 Word。它连接当前本地服务，复用 Store、Worker 和报告生产流程，不另开一套研究系统。

本功能先提供 CLI 和可分发 Skill，适用于能执行本机命令的 WorkBuddy 或其他 Agent。尚不代表已在 WorkBuddy 平台上架插件；不提供公网服务或远程 MCP 服务端。

## 开始使用

1. 在 BriefLoop 打开需要操作的工作区，添加材料并配置实际可用的模型。企业内部报告先完成是否维护企业背景的选择。
2. 外部 Agent 使用同一台机器上已安装的 `briefloop`，执行 `briefloop external --workspace "/absolute/workspace" discover`，确认 `ready=true`。普通目录不会被自动变成工作区，未启动的服务也不会自动启动。
3. 执行 `briefloop external --workspace "/absolute/workspace" skill` 获取随包说明。需要安装到 Agent 的 Skills 目录时，将输出按 UTF-8 保存为 `briefloop-external/SKILL.md`；具体目录遵循宿主自身配置。输出说明不访问工作区。
4. 按 Skill 创建 UTF-8 JSON 请求文件，执行 `briefloop external --workspace "/absolute/workspace" request --file request.json`。CLI 返回 JSON；错误退出码为 2。

## 任务和版本

只读操作为 `inspect`、`source`、`query`、`read`；写操作为 `submit`、`revise`、`export`，必须携带稳定的 `request_id`。同一请求的任务入队、版本保存与幂等记录在同一数据库事务中提交。响应丢失后重发原请求会返回原 ID；同一 request_id 使用不同内容会报冲突。

`submit` 返回已接收的 job_id 和 run_id，研究异步进行，沿用工作区当前的模型和既有冻结规则。`query` 区分当前任务状态、已保存稿件与最新版本。失败有实际错误文本，不能将已有草稿理解为任务完成。

`revise` 接收完整富文档 `editor_document` 与 `base_version`；复用网页编辑的版本冲突检查，保留原稿。它是保存外部 Agent 已完成的修订，不是另一个自动改写模型调用。读取返回 Markdown 供阅读，修订以富文档为准，以保留图表、图片、引用和样式。

`export` 生成明确 version_id 的工作稿；文件 Worker 执行后，`query` 校验文件及哈希，`download` 再校验下载内容。重复请求不创建新的导出，不覆盖用户已有的不同文件。正式交付审核仍在 BriefLoop 的原有流程中执行，普通工作稿下载不会豁免审核。

## 服务范围

接口只连接已验证身份的本机工作区服务，复用 Host 校验、会话 Token 和退出停止接收请求的逻辑。CLI 不打印 Token，不读取模型凭据。服务端每次请求再次检查 workspace_id；工作区更换或服务未就绪会明确失败。

接口细节和可复制的请求示例以随包的 [Skill](../src/briefloop/skill_assets/briefloop-external/SKILL.md) 为准。`inspect` 默认返回最近 100 个来源和 20 个稿件；已知历史 ID 仍可精确读取。
