# BriefLoop 0.20.0 macOS arm64 桌面版

Electron 薄壳使用 App 自带的 Node 运行桥；Python 来自用户本机，BriefLoop 依赖安装在 App 专属虚拟环境。Mac 使用普通 DMG；当前构建不含完整 Python 或额外 Node。

## 安装与更新

从 [官方 Releases](https://github.com/Stahl-G/briefloop/releases) 选择与发布说明对应的 `BriefLoop-0.20.0-arm64.dmg`。打开 DMG，将 BriefLoop 拷贝到应用程序目录；更新已有安装时先退出 App，再替换原路径的 App。工作区中的报告、对话与附件独立于应用保存。

启动后先检测本机 Python。已有 Python 3.12 等兼容版本即可复用，不要求另外安装 3.11，也不要求为 BriefLoop 单独安装 Node。首次准备仅把 BriefLoop 依赖装入 App 专属环境；缺少 Python 时通过界面打开 python.org 安装入口，然后重新检测。就绪后可新建工作区或继续原工作区。

设置 → App 版本与更新可以检查官方稳定版本、下载并查看进度。macOS 当前使用“保存并打开 DMG”，随后在 Finder 完成替换；只会在你发起安装并完成保存/任务处理后退出。正式源对相同版本显示当前已是最新版本；重新安装同版本可从官方发布页重新下载 DMG。协议与失败恢复见 [UPDATES.md](UPDATES.md)。

## 构建

构建机准备 Python 3.11+、Node/npm。发布构建前先确认 `pyproject.toml`、本目录 `package.json` / `package-lock.json` 以及待打包 wheel 清单的版本均为 `0.20.0`。从仓库根目录执行：

```sh
npm ci
npm run build
python3 desktop/electron/scripts/prepare-backend.py
cd desktop/electron
npm ci
npm run dist
```

准备脚本在构建专属 venv 中安装 pyproject 声明的构建依赖，再从当前 `src/` 和许可文件生成普通 wheel；不使用 editable 安装或个人源码路径。输出 `backend/manifest.json`（版本、wheel 文件名、SHA-256）和 wheel，整体放在 App 的 `Contents/Resources/backend/`，ASAR 外。每次业务或静态资源变化后重新生成 wheel。

`npm run dist` 生成 `dist/BriefLoop-0.20.0-arm64.dmg` 和 `dist/BriefLoop-0.20.0-arm64-mac.zip`。Electron 核心仍需随 App 分发；首次运行还会从官方 PyPI 下载 Python 依赖，不能将安装包大小作为完整下载量。Python 环境准备契约见 [ENVIRONMENT.md](ENVIRONMENT.md)。生成目录与构建缓存不进 Git。

已有本机 Electron 分发目录时，可在本目录执行 `npm run dist -- --config.electronDist="$PWD/node_modules/electron/dist"` 复用它，只刷新 App 资源和安装包。版本更新后必须重新生成业务 wheel；不能只改安装包文件名或沿用旧版本 wheel。

发行验收记录应分别写明源码提交、App 版本、wheel 哈希、安装包哈希和实际启动结果。0.19 的本地原生链路证据保持原版本归属；0.20 最终包需核对匹配版本和最小启动结果，不把已有记录改称新版本运行结果。

## 首次启动与运行

欢迎页先检测本机兼容 Python，并验证已有 App 专属环境。缺少 Python 时提供固定 python.org 安装入口和重新检测。用户点击“下载依赖并准备”后，App 创建独立 venv、安装随包 wheel 的声明依赖、验证导入和 pip check；就绪后才能打开工作区。界面显示实际阶段，允许取消或失败重试。安装失败保留旧有效环境，工作区独立保存。

后台以 App 专属 Python 启动，桥使用当前 `process.execPath` 指向 Electron 内置 Node。只给桥进程设置 `ELECTRON_RUN_AS_NODE=1`，桥入口立即清除此标记，宿主 CLI 子进程不继承。不得关闭 Electron RunAsNode fuse。外部模型宿主仍须由用户安装和配置，包括宿主自己的运行依赖。

历史 `prepare-runtime.py`、`refresh-app.py` 及 runtime lock 仅用于复查旧全量包证据，不属于当前发行构建流程。当前构建未签名／公证；本地安装验证不代表正式发布或 Windows 原生验收。

## 窗口保存与退出协议

preload 提供 `briefloopDesktop.onPrepareClose(callback)`。main 为每次准备关闭生成随机请求 ID；renderer 通过现有 `savedVersion()`／保存队列完成保存后，只回传 `{status:'saved', version_id:string|null}`，失败则回传 `{status:'failed', error:string}`。失败或 30 秒内未收到确认时保留窗口，不把关闭窗口当保存成功。

保存确认后，main 才调用现有 `service-status` 盘点全部忙碌任务。用户选择继续工作时取消退出；选择停止并退出时使用 `service-stop` 的 `busy_action: 'cancel'`，随后等待自己启动的后台子进程实际退出。窗口和后台的完成状态分别确认。

`openWorkspace({path, create})` 是受限 IPC：main 校验调用窗口、当前 mainFrame 来源和本地路径，不接受命令、URL 或 PID。文件导入沿用同一 Web UI 的原生 file input；`will-download` 弹出系统另存对话框。可安装 `.app`／DMG 的最终位置和窗口操作结果以桌面验收记录为准。

保存握手开始时 renderer 暂设 `body.inert`，防止保存确认与关闭之间继续输入。preload 的固定 `onResume` 回调响应 `workspace:resume`；main 在取消关闭、保存失败或仅完成 Cmd+S 保存时发送它恢复编辑，失败路径也解除 inert。
