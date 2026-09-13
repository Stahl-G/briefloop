# Windows 原生预览

Windows 使用同一套 Python 服务、WebUI、SQLite 和报告流程。目前没有 Electron 安装包，也不把单一宿主的测试结果推广到所有宿主。

## 启动与退出

需要 Python 3.11+。PowerShell 可用 `start.ps1 -Python <python.exe 的路径>` 指定解释器；也可运行 `python -X utf8 bootstrap.py`。启动器、CLI 和 agent 工具均使用 UTF-8，支持中文和空格路径。Bridge 宿主需要 Node.js 20+。

每个工作区先取得排他锁，再打开数据库。虚拟环境启动器与实际服务可能有不同 PID，就绪消息通过本次启动标识关联并记录实际服务 PID。第二实例不能改变原服务设置。

工作区停止经本机服务身份与会话校验关闭，仅回收应用拥有的宿主进程树，不按程序名批量结束进程。任务取消使用宿主取消接口；工作区关闭才回收整个所属宿主。Windows Job Object 用于生命周期管理，不是文件或网络沙箱，也不赋予 Reviewer 受限审阅能力。

## CLI 和导出兼容

支持 npm 的 Node 脚本和原生 exe 两类启动 shim，不经 `cmd.exe` 展开参数。Agent 工具固定到当前服务的 Python 和包入口，不依赖 agent 工作目录。OpenCode 的工具名称 `bash` 不代表实际 shell；自有服务与工具命令使用同一 shell 选择，保持 PowerShell/Git Bash 引用一致。`join-scouts --output` 由 Python 写 UTF-8，避免旧 PowerShell 重定向改变编码。

工作区操作的 JSON 请求文件和 shell 工作目录必须位于当前工作区；系统临时目录不属于“读写工作区”的授权范围。聊天合同提供可直接执行的工作区内 UTF-8 示例，CLI 也兼容旧 PowerShell 写出的 UTF-8 BOM。OpenCode 使用原生 system 字段接收该合同，拒绝合同或权限配置时停止执行；不能把工具失败后写出的聊天正文冒充已保存报告。

模型连接重试在进度面板显示明确状态；停止或切换任务后，旧进度响应不能覆盖新状态。Reviewer 核查包统一使用正斜杠相对路径，仍验证文件清单、哈希、索引和路径边界。

Word 原文件被 Office/WPS 占用而不能替换时，导出保留旧文件并另存已渲染的新文件，不重跑模型。通用默认导出使用 Arial 作为西文字体，中文由阅读器本机字体回退；用户模板保留自身字体，不承诺跨系统字形和分页一致。

## 已验证与边界

本机环境：Windows x64，系统版本 10.0.26200，Python 3.11.16，Node.js 24.16.0，OpenCode 1.18.30。

- 普通 wheel 安装到带中文和空格的独立环境，从源码目录外启动；内联 WikiSkill 和 36 套模板可用。
- 排他锁、真实服务 PID、错误身份停止请求被拒绝、所属进程树回收且无关进程保留。
- 中文及 emoji 材料上传、原文件字节与正文保存、重开数据完整性。
- OpenCode + Muse Spark 1.3 Free 真实网页生成合成周报，实际 Scout/Analyst 执行；编辑自动保存并保留原稿，两次 Word 导出绑定各自版本。
- OpenCode + DeepSeek 官方 `deepseek-flash` 的 CLI 最小请求及网页工具读取均完成；Windows 工具命令首次成功，回复正确区分交付件数与订单单数。服务运行后从外部 CLI 新增凭据，本机测量需重启服务才能刷新提供商目录；首次超时不计为通过。
- DOCX 内部检查确认中英文字、金额、特殊符号、表格与加粗保留；Windows 文件占用句柄下另存原件不变。
- Codex + Luna 的报告请求遇到连接重试，不计为生成通过；随后 Codex 原生 app-server + DeepSeek Flash 的只读合成文件工具回合通过。其他宿主完整报告闭环、学习收益、WPS 实际字体显示：**NOT MEASURED**。

独立审阅发现的 Windows 文件清单分隔符缺陷已通过真实失败包的完整哈希复验与防篡改回归；修复后同一真实审阅任务重跑完成，结果绑定原稿，未错误归入后来编辑的版本。

Windows MCP 已实际验证官方 SDK 的 stdio 与 HTTP 连接、scope 隔离、取消、连接中断和响应大小限制。stdio supervisor 使用 Job 管理所属子孙进程；强制结束 supervisor 后子孙退出，无关进程保留。凭据文件使用受保护的 Windows DACL，仅授权当前用户，并拒绝 reparse point；不以 POSIX 权限位推断 Windows 文件私密性。

网页端的合成 MCP 配置保存、连接测试、启用（6 个工具、2 个资源）和停用通过。应用已接入逐报告选材、冻结任务授权与 Agent 受控材料接口；Windows 上从网页选材到实际 Agent 读取、引用和审阅仍按具体运行记录核对，连接成功本身不代表这条报告链已通过。

Hermes 0.21.2 与 Reasonix 1.38.7 已通过实际 BriefLoop bridge 使用官方 DeepSeek Flash 读取合成文件并完成回复。Hermes 优先使用专用 `hermes-acp` 入口；首次模型目录初始化可能准备额外依赖并超出启动等待时间，本机依赖准备完成后重测通过。Reasonix 新版的模型标识为 `provider/model`，目录保留实际模型部分。上述调用使用隔离测试配置，不代表用户原有模型认证已经配置完成。

同样的真实工具回合也在 Kimi Code 0.42.0、Claude Code 2.1.186、MiMo Code 0.1.0 上完成，使用各宿主支持的临时环境或独立配置连接 DeepSeek 官方 API；模型返回了实际合成文件中的标记，临时凭据随后清理。这些是工具调用通过，不能替代各宿主完整的报告生成、审阅与导出验收。

## 自动化验证

GitHub Actions 包含独立 `windows-native` job：Python 3.11、Node 20、普通 wheel 安装、非源码目录执行、中文路径、进程/锁/编码/导出/审阅包回归和安装包 bridge 协议测试。合成协议测试不需要模型凭据；CI 通过不等于真实模型闭环通过。

开发环境安装 `pip install -e '.[dev]'` 后可执行：

```powershell
python -X utf8 -m pytest -q tests/test_windows_platform.py tests/test_npm_native_shim.py tests/test_agent_commands.py tests/test_review_packet_paths.py tests/test_progress.py tests/test_server_boundary.py
$env:BRIEFLOOP_PYTHON = (Get-Command python).Source
$env:BRIEFLOOP_PROCESS_HELPER = (Resolve-Path src/briefloop/process_host.py).Path
node runtime-bridge/build.mjs
node --test runtime-bridge/bridge.test.mjs
npm.cmd test
```

个人环境路径、凭据、运行材料和详细执行记录不进入 Git。
