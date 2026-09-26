# BriefLoop macOS arm64 桌面版

Electron 薄壳使用 App 自带的 Node 运行桥；Python 来自用户本机，BriefLoop 依赖安装在 App 专属虚拟环境。Mac 使用普通 DMG；当前构建不含完整 Python 或额外 Node。

## 安装与更新

从 [官方 Releases](https://github.com/Stahl-G/briefloop/releases) 选择与发布说明对应的 `BriefLoop-<版本>-arm64.dmg`。打开 DMG，将 BriefLoop 拷贝到应用程序目录；更新已有安装时先退出 App，再替换原路径的 App。工作区中的报告、对话与附件独立于应用保存。

启动后先检测本机 Python。已有 Python 3.12 等兼容版本即可复用，不要求另外安装 3.11，也不要求为 BriefLoop 单独安装 Node。首次准备仅把 BriefLoop 依赖装入 App 专属环境；缺少 Python 时通过界面打开 python.org 安装入口，然后重新检测。就绪后可新建工作区或继续原工作区。

设置 → App 版本与更新可以检查官方稳定版本、下载并查看进度。macOS 应用内更新优先使用经完整性校验的 ZIP，在 Finder 完成替换；官网首次安装继续提供 DMG。只会在你发起安装并完成保存/任务处理后退出。正式源对相同版本显示当前已是最新版本；重新安装同版本可从官方发布页重新下载 DMG。协议与失败恢复见 [UPDATES.md](UPDATES.md)。

## 构建

正式签名与 Apple 公证使用 [SIGNING.md](SIGNING.md) 的 `signing:check` / `dist:signed` 入口。它要求有效 Developer ID 证书与公证凭据；现有未签名资产不会因此自动成为已签名发行。

构建机准备 Python 3.11+、Node/npm。发布构建前先确认 `pyproject.toml`、本目录 `package.json` / `package-lock.json` 以及待打包 wheel 清单的版本均与 `pyproject.toml` 的版本一致。以下为本地开发构建。正式发行必须先从冻结提交构建一次后端 wheel 和 sdist，验证后将同一 wheel 及其版本/哈希 manifest 放入 `backend/`；桌面与 PyPI 复用它，不再运行准备脚本重建同版本 wheel。

本地开发从仓库根目录执行：

```sh
npm ci
npm run build
python3 desktop/electron/scripts/prepare-backend.py
cd desktop/electron
npm ci
npm run dist
```

准备脚本在构建专属 venv 中安装 pyproject 声明的构建依赖，再从当前 `src/` 和许可文件生成普通 wheel；不使用 editable 安装或个人源码路径。输出 `backend/manifest.json`（版本、wheel 文件名、SHA-256）和 wheel，整体放在 App 的 `Contents/Resources/backend/`，ASAR 外。每次业务或静态资源变化后重新生成 wheel。

`npm run dist` 生成 `dist/BriefLoop-<版本>-arm64.dmg` 和 `dist/BriefLoop-<版本>-arm64-mac.zip`。Electron 核心仍需随 App 分发；首次运行还会从官方 PyPI 下载 Python 依赖，不能将安装包大小作为完整下载量。Python 环境准备契约见 [ENVIRONMENT.md](ENVIRONMENT.md)。生成目录与构建缓存不进 Git。

已有本机 Electron 分发目录时，可在本目录执行 `npm run dist -- --config.electronDist="$PWD/node_modules/electron/dist"` 复用它，只刷新 App 资源和安装包。版本更新后必须重新生成业务 wheel；不能只改安装包文件名或沿用旧版本 wheel。

发行验收记录应分别写明源码提交、App 版本、wheel 哈希、安装包哈希和实际启动结果。历史版本的原生链路证据保持原版本归属；每次发行最终包都需重新核对版本及实际启动、导出和更新行为，不把旧记录改称新版本验收。

## 首次启动与运行

欢迎页优先复用已准备且与随包 wheel 匹配的 App 专属环境；首次准备、依赖失效或主动检查时检测兼容 Python。缺少 Python 时提供固定 python.org 安装入口和重新检测。用户点击“下载依赖并准备”后，App 创建独立 venv、安装随包 wheel 的声明依赖、验证导入和 pip check；就绪后才能打开工作区。界面显示实际阶段，允许取消或失败重试。安装失败保留旧有效环境，工作区独立保存。

后台以 App 专属 Python 启动，桥使用当前 `process.execPath` 指向 Electron 内置 Node。只给桥进程设置 `ELECTRON_RUN_AS_NODE=1`，桥入口立即清除此标记，宿主 CLI 子进程不继承。不得关闭 Electron RunAsNode fuse。外部模型宿主仍须由用户安装和配置，包括宿主自己的运行依赖。

历史 `prepare-runtime.py`、`refresh-app.py` 及 runtime lock 仅用于复查旧全量包证据，不属于当前发行构建流程。当前构建未签名／公证；本地安装验证不代表正式发布或 Windows 原生验收。

## 窗口保存与退出协议

桌面一次只管理一个工作区；切换不是同时打开第二个后台。切换前先检查目标目录、工作区标记、运行环境和目录写入，再保存并停止当前工作区。目标服务启动或页面加载失败时，先确认目标后台已退出，再尝试在原端口恢复原工作区；恢复以暂停模式启动，不自动重提中断任务。目标尚未退出或原工作区恢复失败时明确报错，不宣称切换成功。独立 WebUI/CLI 服务不属于这个桌面窗口的管理范围。

桌面服务启动时带有随机 `BRIEFLOOP_LAUNCH_ID` 和 `BRIEFLOOP_DESKTOP_OWNER_PIPE=1`，stdin 管道唯一写端由 Electron main 持有，不传给后代。桌面异常退出导致 EOF 后，由配套 Python 服务执行受控取消和关闭；正常退出仍走保存和服务身份校验。恢复只能保证已经保存的内容，强制结束窗口不能保存尚未落盘的编辑。该合约需要同版 Python 后端支持，不能仅替换旧安装的桌面壳后宣称已具备异常退出清理。

preload 提供 `briefloopDesktop.onPrepareClose(callback)`。main 为每次准备关闭生成随机请求 ID；renderer 通过现有 `savedVersion()`／保存队列完成保存后，只回传 `{status:'saved', version_id:string|null}`，失败则回传 `{status:'failed', error:string}`。失败或 30 秒内未收到确认时保留窗口，不把关闭窗口当保存成功。

保存确认后，main 才调用现有 `service-status` 盘点全部忙碌任务。用户选择继续工作时取消退出；选择停止并退出时使用 `service-stop` 的 `busy_action: 'cancel'`，随后等待自己启动的后台子进程实际退出。窗口和后台的完成状态分别确认。

`openWorkspace({path, create})` 是受限 IPC：main 校验调用窗口、当前 mainFrame 来源和本地路径，不接受命令、URL 或 PID。文件导入沿用同一 Web UI 的原生 file input；`will-download` 弹出系统另存对话框。可安装 `.app`／DMG 的最终位置和窗口操作结果以桌面验收记录为准。

保存握手开始时 renderer 暂设 `body.inert`，防止保存确认与关闭之间继续输入。preload 的固定 `onResume` 回调响应 `workspace:resume`；main 在取消关闭、保存失败或仅完成 Cmd+S 保存时发送它恢复编辑，失败路径也解除 inert。
