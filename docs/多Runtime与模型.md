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
| CodeBuddy Code | ACP 原生模型目录 | 原生 `--acp` |

目录来源会显示在模型区。读取不到宿主目录时给出内置建议，首次真实调用会验证账号权限；页面不会因模型不在目录内而拒绝有效的手填 ID。

检测到程序、可选择执行、协议握手成功和真实模型调用成功是不同状态。CLI 卡片的“可选择”只表示应用实现了该后端且找到本机程序；即使版本探测失败也不代表已完成账号或模型验证。安装列表中的其它 CLI 可能仅用于发现，不能执行。当前应用支持的执行后端仅为上表八个；Kilo、Kiro、Vibe 虽在底层 bridge 有 ACP 接线，尚未开放为应用执行后端。

“测试”会发出一次短模型请求，结果保存在对话中；检测 CLI、读取模型目录不会启动报告。模型、会话及真实工具标识沿用宿主返回值。恢复失败保留原执行记录，不自动重复提交。

新 CLI 使用宿主原生权限，关闭联网向 Agent 表达本轮不主动搜索的要求。受限 Reviewer 仍只使用已经验证了宿主文件、写入、联网和委派限制的通道。

## 输入区只显示宿主真正支持的能力

| 能力 | 表现 |
| --- | --- |
| `permission_modes` | 权限下拉按宿主声明的档位生成；只有一种时不显示这个下拉，不再出现"宿主原生权限"这种抽象选项 |
| `steer` | 只有支持运行中追加的宿主提供"立即补充"（目前是 Codex）；Opencode 与 bridge CLI 只能排队 |
| `images` | 明确声明不支持读图的宿主（如 MiMo）在附图时当场提示并跳过该文件 |
| `network_control` | Codex 之外的宿主没有每轮网络硬开关；"联网"是向宿主放行的要求，最终能否联网由宿主决定 |
| `questions`、`cancel`、`resume` | 宿主支持时才出现对应交互：权限提问卡片、停止按钮、续接同一原生会话 |

"默认"表示使用宿主自己配置的模型。能读到宿主配置时下拉会显示解析结果：Claude Code 读 `~/.claude/settings.json` 的模型别名与 `ANTHROPIC_DEFAULT_*_MODEL` / `ANTHROPIC_MODEL` 映射，例如「默认：deepseek-v4-pro（别名 opus）」；读不到时保持通用标签。下拉末尾的"搜索全部模型…"打开可搜索列表，手填 ID 始终可用。

打开联网会向宿主放行它自己的联网工具：Claude Code 追加 `--allowedTools WebSearch WebFetch`，ACP 宿主走各自的权限协商。放行不等于一定成功，宿主仍可能拒绝；此时错误如实显示，不会假装已经搜索。

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

### CodeBuddy Code

先在本机完成 CodeBuddy 登录，再在设置中选择该宿主和原生模型。后端 ID 为 `codebuddy`，模型字段填写 `deepseek-v4.1-flash` 等原生 ID，不需再加宿主前缀。

macOS 上已用 CodeBuddy 2.150.0 验证网页模型选择、真实对话、原生会话跨进程续接、文件单次授权与读写，以及等待权限时停止；停止后未执行待批准的写入。原生工具缺少标题时，权限提示显示其文件路径。其他平台、目录中的其他模型及图片尚未逐项实测。

当前复用 bridge 的宿主原生权限；独立只读 Reviewer 尚未验证，产品仍拒绝将该角色回退到普通写权限。普通对话验证不代表整份报告及正式交付验收通过。原生 usage 保存在执行事件中；未归一化的统计项显示未知。
