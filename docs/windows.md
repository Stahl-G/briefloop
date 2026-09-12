# Windows 原生预览

Windows 使用同一套 Python 服务、WebUI、SQLite 和报告流程。目前没有 Electron 安装包，也不把单一宿主的测试结果推广到所有宿主。

## 启动与退出

需要 Python 3.11+。PowerShell 可用 `start.ps1 -Python <python.exe 的路径>` 指定解释器；也可运行 `python -X utf8 bootstrap.py`。启动器、CLI 和 agent 工具均使用 UTF-8，支持中文和空格路径。Bridge 宿主需要 Node.js 20+。

每个工作区先取得排他锁，再打开数据库。虚拟环境启动器与实际服务可能有不同 PID，就绪消息通过本次启动标识关联并记录实际服务 PID。第二实例不能改变原服务设置。

工作区停止经本机服务身份与会话校验关闭，仅回收应用拥有的宿主进程树，不按程序名批量结束进程。任务取消使用宿主取消接口；工作区关闭才回收整个所属宿主。Windows Job Object 用于生命周期管理，不是文件或网络沙箱，也不赋予 Reviewer 受限审阅能力。

## CLI 和导出兼容

支持 npm 的 Node 脚本和原生 exe 两类启动 shim，不经 `cmd.exe` 展开参数。Agent 工具固定到当前服务的 Python 和包入口，不依赖 agent 工作目录。OpenCode 的工具名称 `bash` 不代表实际 shell；自有服务与工具命令使用同一 shell 选择，保持 PowerShell/Git Bash 引用一致。`join-scouts --output` 由 Python 写 UTF-8，避免旧 PowerShell 重定向改变编码。

模型连接重试在进度面板显示明确状态；停止或切换任务后，旧进度响应不能覆盖新状态。Reviewer 核查包统一使用正斜杠相对路径，仍验证文件清单、哈希、索引和路径边界。

Word 原文件被 Office/WPS 占用而不能替换时，导出保留旧文件并另存已渲染的新文件，不重跑模型。通用默认导出使用 Arial 作为西文字体，中文由阅读器本机字体回退；用户模板保留自身字体，不承诺跨系统字形和分页一致。

## 已验证与边界

本机环境：Windows x64，系统版本 10.0.26200，Python 3.11.16，Node.js 24.16.0，OpenCode 1.18.30。

- 普通 wheel 安装到带中文和空格的独立环境，从源码目录外启动；内联 WikiSkill 和 36 套模板可用。
- 排他锁、真实服务 PID、错误身份停止请求被拒绝、所属进程树回收且无关进程保留。
- 中文及 emoji 材料上传、原文件字节与正文保存、重开数据完整性。
- OpenCode + Muse Spark 1.3 Free 真实网页生成合成周报，实际 Scout/Analyst 执行；编辑自动保存并保留原稿，两次 Word 导出绑定各自版本。
- DOCX 内部检查确认中英文字、金额、特殊符号、表格与加粗保留；Windows 文件占用句柄下另存原件不变。
- Codex 实际模型请求遇到连接重试；不计为生成通过。其他宿主完整闭环、学习收益、WPS 实际字体显示：**NOT MEASURED**。

独立审阅发现的 Windows 文件清单分隔符缺陷已通过真实失败包的完整哈希复验与防篡改回归；真实审阅重跑结果须另行核对，不能用静态验证替代。

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
