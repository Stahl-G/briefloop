# OpenCode v1 / v2 兼容

BriefLoop 保留 OpenCode 1.x 接口，并为 OpenCode 2.x 使用独立适配层。界面仍选择同一个 OpenCode CLI，程序依据实际可执行文件和所启动服务的版本选择协议，不要求卸载 v1，也不自动安装、降级或迁移 OpenCode。

## 两代协议

| 项目 | v1 | v2 |
|---|---|---|
| 服务检查 | `/global/health` | `/api/info` |
| 会话目录 | 请求的 `directory` 查询参数 | 创建时的 `location.directory`，后续核对绑定 |
| 模型与推理档位 | 会话 ModelRef 和逐次 prompt 的 model/variant | 会话 ModelRef；变更后回读，再发送正文 |
| 任务系统说明 | prompt 的 `system` 字段 | 首次 instruction entry 进入 system；开始执行后仅允许同值续接，变更要求明确新建对话 |
| 会话执行 | `prompt_async` 与消息轮询 | `prompt` 与分页消息投影 |
| 模型目录 | `/config/providers` | `/api/provider` 与 `/api/model` |

v2 删除了 `models --verbose`。产品中的模型选择和推理档位共用 BriefLoop 管理的服务及模型目录，使用所选模型实际公开的 variant；不会猜测固定的 low/high 列表。目录没有该模型、没有公开档位、读取失败是不同状态。

新适配保留目录、模型、推理档位、权限、附件和任务说明；宿主拒绝或未保留这些关键参数时，不删掉参数后重试。旧会话在新宿主里不存在时，返回可解释错误，不静默创建新会话冒充恢复。

## 明确的能力边界

- **普通生成与对话**：v1 保留原路线；v2 接入新的会话协议。
- **系统约定变更**：真实 v2.0.14 的本地兼容供应方请求显示，后续 instruction entry 的修改/删除以 `user` 角色传出，历史初始 system 内容仍保留，并不等价于 v1 每轮重新提供 system。因此本实现会在已开始会话的约定发生变更时拒绝本轮任务，且先于模型、Agent 或条目修改；不自动替换、重建会话或重发。相同约定可继续，同日同设置的普通连续对话不会必然受阻。模型/推理档位、联网状态、搜索策略、工作区基础资料、系统日期/时区或程序升级导致约定变化时，需要明确新建对话。
- **独立审阅**：OpenCode v1 / v2 都可执行普通审阅，使用新独立会话、原生只读工具规则和同一套事实与证据检查。两代宿主都可能自动加载全局或祖先目录说明，因此都不宣称核查包完全隔离。严格审阅额外要求强制包内读取，当前通过 BriefLoop Agent 的专用核查工具执行；用户明确选择严格模式时，不自动改为普通模式或更换后端。
- **提供方配置**：v1 原有配置保存功能保留。已核对的 v2.0.14 没有等价的公开 Provider 写入接口，v2 下须在 OpenCode 中完成配置；BriefLoop 不擅自写宿主私有配置或迁移认证。产品会明确拒绝不支持的保存操作，不返回虚假的成功。
- **旧数据迁移**：如 OpenCode v2 尚未迁移 v1 会话，应在 OpenCode 内按其流程处理，或者明确新建 BriefLoop 对话。BriefLoop 不自动重发旧任务。
- **权限含义**：原生工具规则不是操作系统沙箱。v2 的 `shell` 对应 v1 的 `bash`；写入、编辑和补丁由 `edit` 规则约束。外部目录规则以及联网工具限制的范围沿宿主实际实现说明，不宣称禁止一切外部访问。
- **子任务接口**：v1 使用 `task`；v2 使用 `subagent`，传递 `agent`、`description`、`prompt`，仅在继续已有子任务时传真实 `sessionID`。前台返回 `completed` 后再收取结果；`running` 不算完成。研究分工、材料、预算和默认模型继承策略保持一致。

`briefloop doctor` 返回 `opencode_supported_majors`，表示软件适配范围；检测到命令及版本不等于已经登录、模型可调用或完整报告通过验收。

## 实现与验证依据

协议基于 OpenCode **1.18.31** 的实际服务及 **2.0.14** 的实际服务和同版本官方接口。没有把 SDK 路径中的“v2”与可执行程序的主版本混为一谈。

官方来源：

- [OpenCode v2.0.14](https://github.com/anomalyco/opencode/tree/v2.0.14)
- [v2 会话接口](https://github.com/anomalyco/opencode/blob/v2.0.14/packages/server/src/handlers/session.ts)
- [系统说明装配](https://github.com/anomalyco/opencode/blob/v2.0.14/packages/core/src/session/model-request.ts)
- [原生指令条目](https://github.com/anomalyco/opencode/blob/v2.0.14/packages/core/src/session/instruction-entry.ts)
- [权限执行](https://github.com/anomalyco/opencode/blob/v2.0.14/packages/core/src/permission.ts)

测试区分协议单元检查、隔离的真实 CLI 服务检查，以及真实模型报告。合成服务或本地模拟供应方用于验证接线，不作为真实模型质量、跨平台桌面或正式交付通过的证据。具体结果随该功能的验收记录补充。

## 普通与严格审阅

设置中的审阅模式与执行后端分开选择，新请求默认普通审阅。普通模式目前接入 Codex CLI、OpenCode CLI 和 BriefLoop Agent；严格模式目前只由 BriefLoop Agent 提供。新增能力以服务返回的实际能力列表为准，界面不通过版本号推测隔离能力。

普通与严格使用相同的原稿、来源、版本绑定和问题接纳规则。重大事实错误、核心证据缺口和未决重要冲突仍阻止正式交付；严格不等于事实必然正确，普通也不是降低质量标准。

新任务冻结模式与实际后端，结果页显示实际记录。后改设置只影响新请求。历史缺字段的记录显示“历史审阅（未记录模式）”，不因引擎名称补写为严格，也不重写旧报告与有效审阅。

## 本次验证边界

在 macOS 上使用真实 OpenCode 1.18.31 和 2.0.14、隔离的自建配置和本地合成供应方，验证了服务认证、会话与模型 High 档位、读取回执、拒绝写入、中文消息、用量投影、空子会话列表、空闲取消及自有进程清理。另跑通 v2 Harness 到 ChatStore 的中文消息保存。没有使用用户凭据或外部付费模型。

这些验证证明协议接通，不是完整报告质量验收。未声称覆盖长任务取消、所有权限逃逸、真实多子 Agent 报告或 Windows 桌面；Windows npm shim 转换另用行为测试检查。后续系统约定拒绝、审阅模式与版本字段恢复由定向行为测试验证，具体集成结果见验收记录。
