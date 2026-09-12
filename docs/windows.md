# Windows 原生预览

Windows 使用现有 Python 服务、WebUI、SQLite 和报告流程，不另建业务后端。当前没有 Electron 安装包，也不把基础启动成功当作所有宿主已支持。

## 启动与退出

需要 Python 3.11+。PowerShell 可用 `start.ps1 -Python <python.exe 的路径>` 指定解释器；也可运行 `python -X utf8 bootstrap.py`。启动器与 CLI 使用 UTF-8，兼容中文和空格路径。Bridge 宿主还需要 Node.js 20+。

每个工作区先取得排他锁，再打开数据库。Windows 使用操作系统字节锁，不跳过互斥检查。虚拟环境启动器与实际 Python 服务可能有不同 PID，因此就绪消息以本次唯一启动标识关联，并记录服务实际 PID。第二实例不会更改原服务的设置。

网页切换到另一工作区后，可在设置中停止原工作区。Windows 经本机服务身份与会话校验请求关闭，释放应用拥有的宿主进程树，不按程序名批量结束进程。任务取消仍走各宿主的取消接口；工作区关闭才回收整个所属宿主。

Windows Job Object 只用于进程归属和回收，不是文件或网络沙箱，也不赋予 Reviewer 受限审阅能力。

Word 文件被 Office/WPS 占用而不能替换时，导出任务保留旧文件并另存已经渲染的新文件；不会重跑模型。该行为不等于复杂 Word 版式已在所有阅读器验证。

## 本批验证范围

在 Windows x64、系统版本 10.0.26200、Python 3.11.16、Node.js 24.16.0 下验证：

- 本地 wheel 安装到仓库外环境；内联 WikiSkill 与 36 套模板可导入。
- 网页加载合成示例，中文修改自动保存，保留原稿与修订。
- 中文与 emoji 材料保存、DOCX 工作稿导出、退出重开后内容保留。
- 第二实例拒绝且不能改变原服务配置；关闭所属进程树保留无关进程。
- Windows 文件占用句柄下另存 Word，原件不变；本机确认 Word 可执行文件。WPS 仅发现服务组件，编辑器安装未确认。
- Codex CLI 初始化与关闭，无模型请求；bridge 协议行为使用合成 CLI fixture 验证。

真实研究生成、独立审阅、学习收益及其他宿主的真实模型闭环：**NOT MEASURED**。未发布安装包，不宣称全面 Windows 兼容。Office/WPS 的真实交互排版仍需另行验证。

## 开发验证

先在虚拟环境安装 `pip install -e '.[dev]'`，再用该环境的 Python 执行：

```powershell
python -X utf8 -m pytest -q tests/test_windows_platform.py tests/test_server_boundary.py tests/test_workspaces.py tests/test_runtime_bridge.py
$env:BRIEFLOOP_PYTHON = (Get-Command python).Source
$env:BRIEFLOOP_PROCESS_HELPER = (Resolve-Path src/briefloop/process_host.py).Path
node runtime-bridge/build.mjs
node --test runtime-bridge/bridge.test.mjs
npm.cmd test
```

本地详细执行记录和个人环境路径不进入 Git。
