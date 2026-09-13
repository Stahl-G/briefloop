# BriefLoop Runtime Bridge（本地试点）

Node 20+ 运行 `node src/briefloop/static/runtime-bridge.mjs`。分发文件已经构建，不依赖 Open Design 源码、npm 安装或机器私有目录。开发者在仓库 `npm ci` 后执行 `node runtime-bridge/build.mjs`。

复用 Open Design 的实际 ACP 参数构建、模型目录归一化和容错 JSON 流解析；Apache 原件与提交在 `third_party/open-design/`。没有复制上游设计提示词、权限自动批准、跳过权限、遥测或完整 daemon。`catalog.json` 由上游 runtime 定义提取，不能把被检测到等同于可执行。

## 接口

stdin/stdout 各一行 JSON：请求 `{id,method,params}`，应答 `{id,result}` 或 `{id,error:{message}}`。异步消息 `{method:"event",params:{execution_id,kind,...}}`。

- `discover {paths?:{runtime_id:absolute_path}}`：PATH 与常见用户安装目录检测、限时版本探测；不登录、不安装、不推理。返回已安装与未安装 runtime 列表。
- `list_models {runtime_id,path?,cwd?}`：ACP initialize/session-new 取得宿主目录；MiMo/OpenCode `models`；Claude 仅宿主默认项及明确的 `host_default_only`，支持直接输入模型 ID。不会伪造在线模型列表。
- `start {execution_id,runtime_id,cwd,prompt,model?,session_id?,images?:[absolute_path],permission:"runtime-native",allow_web:null,path?,timeout_ms?}`：快速应答，随后发送事件。没有成功结果的退出判为失败。
- `cancel {execution_id}`：发送 ACP cancel 并终止该执行拥有的进程组。
- `answer {execution_id,request_id,option_id?}`：回应 ACP 权限请求。只接受宿主列出的 optionId；不传代表取消，不自动批准。

事件：`text {text,delta:true}`、`reasoning {text,delta:true}`、`session {session_id,capabilities?}`、`tool {id,name,status,input?,output?}`、`question {request_id,type,title,options}`、`usage {usage,cost?}`、`error {message}`、`end {status:"completed"|"failed"|"cancelled",error?}`。

## 实现状态

| Runtime | 传输 | 说明 |
|---|---|---|
| Kimi、Hermes、Reasonix | ACP | 已接入；模型/图片/恢复依据实际握手，权限逐次提问 |
| Kilo、Kiro、Vibe | ACP | 共用传输已接线，安装状态及实际能力仍需实机验证 |
| Claude | stream-json | 已接入文本/工具/原生图片/恢复；使用宿主原生权限模式与 stdio 授权回答，无自动权限批准 |
| MiMo | run --format json | 保留原生流；通过 --agent 应用宿主模式。当前接口不能交互授权；本机默认 mimo-auto 返回 HTTP 400，ACP 短调用超时，未据此替换原有运行传输 |
| Codex、OpenCode | 原有 native manager | 本 bridge 检测它们；Python facade 负责分发给已存在的执行管理器 |
| Antigravity | stream-json | 使用 agy 正式无界面协议，支持文本/工具/指定会话续接；遵守宿主原生权限，未提供 headless 权限回答 |
| DeepSeek Harness | ACP | 使用已安装 dsh 的 acp profile；profile 初始化由宿主处理，实际模型调用需单独验证 |
| 其他已知 CLI | 仅检测 | 未实现执行，不显示为已接通 |

ACP `agent_thought_chunk`（以及 Claude `thinking` 块、Opencode `reasoning` part）作为独立的 `reasoning` 事件传给本地聊天展示，不与可见正文或工具事件混在一起；执行日志、通知与审计包仍由 BriefLoop 的既有记录层排除推理。并不将此通道宣称为通用敏感资料脱敏器；执行日志和审计包仍由 BriefLoop 的现有记录层处理。

新增宿主使用 **宿主原生权限**：`read-only`、`workspace-write` 等宿主未验证的保证在启动前拒绝。`allow_web:null` 表示宿主管理网络；应用另按本轮要求指示是否主动检索，不能把指令当成网络隔离。不用提示词冒充禁止联网、不把原生权限叫成工作区隔离。受限 Reviewer 应选择已经能执行核查边界的原生管理器。ACP 的恢复与图片能力直到 handshake 才知道，静态字段为 `negotiated`，前端不能把它视为无条件 true。

## 最小验证

`node --test runtime-bridge/bridge.test.mjs`：合成协议行为覆盖宿主模型/会话、Hermes 两种入口、Reasonix 多模型标识、权限回答、推理独立事件通道、限制拒绝、取消和失败状态。测试不调用真实模型。每个本机 CLI 的短真实调用由试点验收记录单独说明，不以协议 fixture 宣称实机成功。

模型目录直接复用上游 ACP、Codex 和 OpenCode 解析函数及 Claude 本机路由发现。Reasonix 使用原生 doctor 模型配置。内置建议标注来源，不作为选择白名单；用户仍可手填模型。

DeepSeek Harness uses `dsh --profile acp` with its existing provider credentials. ACP session continuation prefers advertised `session/load`, or uses `session/resume` when advertised instead; unsupported continuation fails before prompting. Model IDs come from the host catalog.

Antigravity 协议依据 https://antigravity.google/docs/cli/headless/；只把 SUCCESS 且正常退出的结果记为完成，累计会话用量不作本轮上下文输入。

Antigravity 使用本机已有登录和原生权限。无界面调用不能回答交互权限请求；需要批准的工具可能被宿主 soft-deny，即使宿主返回 SUCCESS。桥接会保留工具错误，并把仅有失败工具且无正文的回合记为失败。请在 BriefLoop 权限面板或宿主官方 permissions.allow 配置明确的文件/工具范围；BriefLoop 不自动添加全局授权，也不传递跳过权限的参数。图片输入暂不支持。

Pi 使用已安装 CLI 的 `--mode rpc`，按 `get_available_models` 返回的 provider/model 精确选择，使用原生 sessionFile 续接。等待 `agent_settled` 结束回合；扩展 select/confirm 请求由用户回答，文本型扩展输入尚未接入，图片暂不支持。协议来源为 Pi 随包 docs/rpc.md。

## 对话权限管理

对话输入框的“权限”按钮按实际宿主能力显示控制：Codex/OpenCode 保留确定的工作区读写/只读模式；ACP 从握手读取可选模式并通过 `session/set_mode` 应用，原生确认请求继续经 `answer` 回传；Claude 使用原生 permission-mode 和 stdio can_use_tool；Pi 通过 `--no-tools` 或 `--tools read,grep,find,ls` 配合 `--no-extensions` 控制工具集合。这些模式不冒充操作系统级沙箱。

Antigravity 规则编辑仅修改用户提交的精确 allow/ask/deny 条目，保留其他原生设置；使用文件版本冲突检查和原子替换。全局规则改变后，已排队的旧权限任务拒绝自动执行，要求用户确认后重新发送。宿主自身的拒绝规则不会被静默移除。

Pi 的 input 不含 cacheRead/cacheWrite；上下文显示把三者合计为本次输入，并从实际选择的模型信息读取窗口上限。对话联网开关移入“运行与搜索”，新对话默认开启，旧草稿和已排队消息保留明确的选择。
