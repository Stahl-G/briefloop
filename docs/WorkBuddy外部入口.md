# WorkBuddy 与本地 Agent 外部入口

外部 Agent 可以使用 BriefLoop 的现有工作区提交报告、查看进度、读取材料和稿件、保存修订、下载指定版本的 Word。它连接当前本地服务，复用 Store、Worker 和报告生产流程，不另开一套研究系统。

本功能提供 CLI、可分发 Skill 和本机 stdio MCP（MCP 随下一版发布），适用于能执行本机命令或启动 stdio 服务的 WorkBuddy 或其他 Agent。尚不代表已在 WorkBuddy 平台上架插件；不提供公网服务或远程 MCP 服务端。

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

## 本机 stdio MCP

先在 BriefLoop 打开用户选择的工作区，再在 MCP 客户端中配置以下 stdio 命令。安装包含 MCP 服务的下一版 BriefLoop 后可使用：

```json
{
  "mcpServers": {
    "briefloop": {
      "command": "briefloop",
      "args": ["mcp", "--workspace", "/absolute/workspace"]
    }
  }
}
```

若宿主找不到 `briefloop`，将 command 改为已安装 BriefLoop 的 Python 可执行文件绝对路径，args 改为 `["-m", "briefloop", "mcp", "--workspace", "/absolute/workspace"]`。无需配置 API Key、Token、服务 URL 或额外依赖。该进程的 stdout 只用于 MCP 协议，诊断走 stderr。

启动时固定规范化绝对路径和工作区 ID，工具不接受工作区、URL 或 Token 参数。现有 external capabilities 返回仅当前服务的 `workspace_path`（规范化绝对路径）和 `workspace_id`；客户端同时核对路径、身份及服务 PID，字段缺失也拒绝连接。复制目录即使保留原 UUID 和 server.json，也不能借此操作原工作区；指向同一真实目录的别名仍可使用。目录不存在、后台未启动或工作区身份变化时，工具返回实际不可用状态；不会转连别的工作区。工作区身份替换后须由用户重新连接。停止 MCP 子进程不关闭 BriefLoop 后台，取消调用也不回滚已接收任务；重连后查询原 job_id，未知写结果使用原 request_id 和原内容重试。

| 工具 | 参数 | 操作 |
|---|---|---|
| `briefloop_discover` | 无 | 只读：检查现有服务、ready 和 external 能力 |
| `briefloop_inspect` | 无 | 只读：最近来源、稿件和实际 ID |
| `briefloop_source` | `source_id` | 只读：来源正文和出处 |
| `briefloop_submit` | `request_id`、`requirements`、可选 `source_ids` | 写入：立即返回 `job_id`、`run_id` |
| `briefloop_query` | `job_id` | 只读：进度、版本和安全错误 |
| `briefloop_read` | `version_id` | 只读：Markdown 和完整富文档 |
| `briefloop_revise` | `request_id`、`base_version`、`editor_document` | 写入：保存明确基础版本的修订 |
| `briefloop_export` | `request_id`、`version_id` | 写入：立即返回 Word 导出 `job_id` |
| `briefloop_download` | `job_id`、`output` | 写入：保存已完成 Word 到明确本机文件路径 |

所有工具拒绝额外字段；`request_id` 和需求字段沿用 external 协议。工具目录准确标注读写、幂等和外部交互：submit 可按工作区配置调用模型与联网；其余工具只操作本机保存数据。成功结果同时提供 JSON `structuredContent` 与同内容的文本块；失败返回 `isError=true` 和安全的 `status/code/message`，不回传原异常字符串或嵌套验证的 input_value。409 冲突明确提示重新核对 request_id 或读取最新版本。discovery 的 `ready=false` 是正常发现结果；报告失败是 query 返回的任务状态，不是 MCP 传输失败。

调用顺序：discover → inspect/source → submit → query；修订使用 read → revise → read；Word 使用 export → query 至 `artifact_available=true` → download。`output` 指明确文件路径且父目录已存在；不覆盖不同内容。MCP 只在请求期间执行短的本地操作，不保持一个调用等待报告生成，也不自动重试写请求。

无模型行为验收可运行 `python -m pytest tests/test_external_mcp.py tests/test_external_requests.py -q`：官方 MCP SDK 启动真实 stdio 子进程（含现代发现和传统 initialize 握手），连接临时本机 HTTP 服务，预留全部模型槽位使报告停留队列，读取合成来源和预存稿件，保存修订，由实际文件 Worker 导出并校验 Word，验证重连幂等和错误投影。此检查不证明模型生成与独立审阅通过；它们须在实际可用模型配置下另行验收。

## 服务范围

接口只连接已验证身份的本机工作区服务，复用 Host 校验、会话 Token 和退出停止接收请求的逻辑。CLI 不打印 Token，不读取模型凭据。外部 action 和客户端文件下载再次检查 workspace_id；工作区更换或服务未就绪会明确失败。

私有 GET（包括握手、材料、稿件和所有下载接口）拒绝明确的外站 Origin，以及 Fetch Metadata 中的跨源请求；同机不同端口也属于跨源。根页和静态界面仍可从外部链接打开，再从工作区内下载。正常同源链接、地址栏导航和不带这些浏览器头的本机 CLI 保持可用，不在下载 URL 中放 Token。

会话 Token 用于 JSON POST 的浏览器请求防护；下载 GET 不要求自定义 Token 头。外部客户端先通过带 Token 的 action/query 校验产物，再流式下载并核对哈希。这不是同机用户或进程之间的权限隔离：能访问本机服务的原生进程可以请求握手 Token；Origin/Fetch Metadata 也不是原生进程的身份证明。

接口细节和可复制的请求示例以随包的 [Skill](../src/briefloop/skill_assets/briefloop-external/SKILL.md) 为准。`inspect` 默认返回最近 100 个来源和 20 个稿件；已知历史 ID 仍可精确读取。
