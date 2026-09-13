# Windows 原生桌面与 CLI

Windows 桌面应用使用 Electron，复用同一套 Python 服务、WebUI、SQLite 和报告流程。当前构建目标是原生 Windows x64，提供标准的当前用户 NSIS 安装包；支持选择安装目录，并创建桌面和开始菜单快捷方式。构建与安装验收步骤见 [Windows 桌面构建说明](../desktop/electron/WINDOWS.md)。单一宿主或开发环境的测试结果不代表所有宿主、所有安装包均已通过验收。

## 安装与首次准备

安装包携带 Electron 和经过清单校验的 BriefLoop 后端 wheel，不再附带完整 Python 或另一份 Node。应用的 bridge 使用 Electron 内置 Node。模型 CLI 及其认证由用户管理；某个 CLI 自身要求的 Node 或其他运行环境仍须满足该 CLI 的要求。

首次启动检测已有 Python 3.11+。未找到时，界面提供 Python 官方下载页面，用户安装后重新检测；应用不会自动安装系统 Python。准备操作在应用用户数据目录下建立专属 venv，将后端 wheel 和依赖安装进去，完成模块导入及 `pip check` 后才允许打开工作区。不会向宿主 Python 或全局环境安装这些依赖，也不依赖开发仓库路径。

首次依赖准备需要联网访问 PyPI，并要求该 Python 版本有可用的依赖 wheel。已有环境再次启动时会重新验证；升级准备失败不会替换之前选定的环境。当前不承诺 Windows ARM64、32 位 Python、所有未来 Python 版本或首次完全离线准备可用。

环境准备使用系统 Windows PowerShell 和 `Add-Type` 加载随包的进程监督程序，以非交互、隐藏窗口方式运行。脚本执行选项仅作用于该子进程，不修改全局 PowerShell 执行策略；系统策略禁止加载时会明确报错。中文和空格路径已验证，极长路径仍受 Windows 长路径设置及 Python 依赖工具限制。

## 启动与退出

安装后的桌面应用从快捷方式启动，环境就绪后新建或打开工作区。开发者仍可在 PowerShell 使用 `start.ps1 -Python <python.exe 的路径>` 指定解释器，或运行 `python -X utf8 bootstrap.py`；这条源码启动路径的 bridge 需要单独的 Node.js 20+。启动器、CLI 和 agent 工具使用 UTF-8。

每个工作区先取得排他锁，再打开数据库。Windows 桌面服务直接启动已验证的基础 Python，并仅向该子进程传入 venv 启动标识，保留专属 venv 的模块路径，使应用持有的子进程 PID 与实际服务 PID 一致。就绪校验仍要求本次启动标识、确切 PID 和本机地址匹配；第二实例不能改变原服务设置。

环境检测、venv 和依赖安装进程先以暂停状态创建，加入应用拥有的 Windows Job Object 后才运行。普通非零退出在确认所属进程树清理后按检查或安装失败处理，失败的解释器候选可继续尝试后续候选。取消、父连接断开及监督进程退出均有所属进程树清理机制；如果丢失整棵树已退出的确认，界面保留保护状态并给出恢复提示，不把主进程退出当成清理成功。

工作区停止经本机服务身份与会话校验关闭，仅回收应用拥有的宿主进程树，不按程序名批量结束进程。任务取消使用宿主取消接口；工作区关闭才回收整个所属宿主。Windows Job Object 用于生命周期管理，不是文件或网络沙箱，也不赋予 Reviewer 受限审阅能力。

## CLI 和导出兼容

支持 npm 的 Node 脚本和原生 exe 两类启动 shim，不经 `cmd.exe` 展开参数。Agent 工具固定到当前服务的 Python 和包入口，不依赖 agent 工作目录。OpenCode 的工具名称 `bash` 不代表实际 shell；自有服务与工具命令使用同一 shell 选择，保持 PowerShell/Git Bash 引用一致。`join-scouts --output` 由 Python 写 UTF-8，避免旧 PowerShell 重定向改变编码。

工作区操作的 JSON 请求文件和 shell 工作目录必须位于当前工作区；系统临时目录不属于“读写工作区”的授权范围。聊天合同提供可直接执行的工作区内 UTF-8 示例，CLI 也兼容旧 PowerShell 写出的 UTF-8 BOM。OpenCode 使用原生 system 字段接收该合同，拒绝合同或权限配置时停止执行；不能把工具失败后写出的聊天正文冒充已保存报告。

模型连接重试在进度面板显示明确状态；停止或切换任务后，旧进度响应不能覆盖新状态。Reviewer 核查包统一使用正斜杠相对路径，仍验证文件清单、哈希、索引和路径边界。

Word 原文件被 Office/WPS 占用而不能替换时，导出保留旧文件并另存已渲染的新文件，不重跑模型。通用默认导出使用 Arial 作为西文字体，中文由阅读器本机字体回退；用户模板保留自身字体，不承诺跨系统字形和分页一致。

## 已验证与边界

下面保留已有单项运行记录，其环境与结果不构成当前安装包的完整安装、字体显示或原生更新验收结论。这些验收必须绑定具体构建产物，不能由源码测试或旧安装包结果替代。

已有后端运行记录的本机环境：Windows x64，系统版本 10.0.26200，Python 3.11.16，Node.js 24.16.0，OpenCode 1.18.30。

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

桌面环境测试另覆盖真实 Windows Job 进程树：失败候选继续检测、取消、主进程失败、父连接断开和监督进程被结束时回收所属子孙，且保留无关进程；并检查直接基础 Python 启动仍使用专属 venv、UTF-8 参数、错误恢复界面及更新安装前的保存与任务门槛。它们不替代安装版窗口、WPS 或实际更新安装测试。安装桌面开发依赖后，可在 `desktop/electron` 下执行 `npm.cmd test`。

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
