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

事件：`text {text,delta:true}`、`session {session_id,capabilities?}`、`tool {id,name,status,input?,output?}`、`question {request_id,type,title,options}`、`usage {usage,cost?}`、`error {message}`、`end {status:"completed"|"failed"|"cancelled",error?}`。

## 实现状态

| Runtime | 传输 | 说明 |
|---|---|---|
| Kimi、Hermes、Reasonix | ACP | 已接入；模型/图片/恢复依据实际握手，权限逐次提问 |
| Kilo、Kiro、Vibe | ACP | 共用传输已接线，安装状态及实际能力仍需实机验证 |
| Claude | stream-json | 已接入文本/工具/原生图片/恢复；使用宿主默认权限，无自动权限批准；未提供 headless 权限回答 |
| MiMo | run --format json | 已接入文本/工具；已接入原生会话恢复；图片直发尚未验证 |
| Codex、OpenCode | 原有 native manager | 本 bridge 检测它们；Python facade 负责分发给已存在的执行管理器 |
| 其他已知 CLI | 仅检测 | 未实现执行，不显示为已接通；DSH 需要另外验证已安装 profile，当前不自动创建 profile |

ACP `agent_thought_chunk` 与其他隐藏推理不进入事件。只传明确的可见消息、工具活动和用量。并不将此通道宣称为通用敏感资料脱敏器；执行日志和审计包仍由 BriefLoop 的现有记录层处理。

新增宿主使用 **宿主原生权限**：`read-only`、`workspace-write` 等宿主未验证的保证在启动前拒绝。`allow_web:null` 表示宿主管理网络；应用另按本轮要求指示是否主动检索，不能把指令当成网络隔离。不用提示词冒充禁止联网、不把原生权限叫成工作区隔离。受限 Reviewer 应选择已经能执行核查边界的原生管理器。ACP 的恢复与图片能力直到 handshake 才知道，静态字段为 `negotiated`，前端不能把它视为无条件 true。

## 最小验证

`node --test runtime-bridge/bridge.test.mjs`：4 个合成协议行为覆盖宿主模型/会话、权限回答、隐藏推理排除、限制拒绝、取消和失败状态。测试不调用真实模型。每个本机 CLI 的短真实调用由试点验收记录单独说明，不以协议 fixture 宣称实机成功。

模型目录直接复用上游 ACP、Codex 和 OpenCode 解析函数及 Claude 本机路由发现。Reasonix 使用原生 doctor 模型配置。内置建议标注来源，不作为选择白名单；用户仍可手填模型。
