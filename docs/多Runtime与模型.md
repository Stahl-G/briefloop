# 多 Runtime 与模型设置

设置页面与报告页面共用 WebUI。未来 Electron 使用同一套页面和本地 API，桌面专属的窗口、文件选择与服务启动放在宿主层。

## 本机 CLI

设置 → 模型与提供商 → 本机 CLI。页面检测已经安装的执行引擎；选择卡片后，自动读取模型目录，也可以输入自定义模型 ID。切换执行引擎后使用新会话，已有会话继续绑定原引擎。

| 执行引擎 | 模型目录来源 | 执行通道 |
| --- | --- | --- |
| Codex | 原生 `debug models` | app-server |
| OpenCode | 本机 provider/model 配置 | 本地原生 HTTP 服务 |
| Claude Code | 本机 MMD 路由；没有路由时显示内置建议 | stream-json |
| Kimi、Hermes | ACP 原生模型目录；读取失败时显示内置建议 | ACP |
| Reasonix | 原生 `doctor --json` 中的模型配置 | ACP，启动参数选择模型 |
| MiMo | 原生 `models --verbose` | JSON 事件流 |

目录来源会显示在模型区。内置建议是选择提示，不代表账号已经具备该模型权限。页面不会因模型不在目录内而拒绝有效的手填 ID。

“测试”会发出一次短模型请求，结果保存在对话中；检测 CLI、读取模型目录不会启动报告。模型、会话及真实工具标识沿用宿主返回值。恢复失败保留原执行记录，不自动重复提交。

新 CLI 使用宿主原生权限，关闭联网向 Agent 表达本轮不主动搜索的要求。受限 Reviewer 仍只使用已经验证了宿主文件、写入、联网和委派限制的通道。

## API 提供商

设置 → 模型与提供商 → API 提供商。可保存多份配置，包括名称、Provider ID、协议、Base URL、API Key 和模型 ID。密钥由本机 OpenCode 凭据存储保存，页面不回显，报告任务只引用 provider/model。

支持显式区分 Chat Completions、Responses 和 Anthropic Messages。协议决定 SDK 和请求路径，不能仅靠更换服务商名称判断。上下文上限与输出上限分别填写；留空不生成虚构的模型能力。

打开已有配置会自动读取远端模型目录。保存配置与选用模型分开；读取目录不进行推理。短工具测试通过 OpenCode 读取隔离目录中的合成文件，执行进度与结果进入同一套对话记录，遵循工作区执行设置，可随时停止。

自定义 API 由工具型 runtime 执行研究和文件操作；不会另建一个纯聊天模型循环。

## 开发与验证

Python Store 继续保存会话、报告、任务、证据和学习记录。新增 CLI 通过每服务一个 Node.js 20+ bridge 交换 NDJSON；标准输出只写协议消息。

```sh
npm ci
node runtime-bridge/build.mjs
npm run build
node --test runtime-bridge/bridge.test.mjs
pytest tests/test_bridge_harness.py tests/test_opencode_provider.py
```

分发使用已经打包的 `runtime-bridge.mjs`，不依赖 OpenDesign 源码所在位置。复用文件、上游版本和本地改动见 `third_party/open-design/NOTICE.md`，附带 Apache-2.0 许可证。
