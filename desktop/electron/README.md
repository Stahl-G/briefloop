# BriefLoop macOS arm64 桌面构建

这是现有 BriefLoop Web UI／Python 后台的 Electron 薄壳。当前运行时只面向 Apple Silicon；构建和搬迁检查不调用模型，不代表模型宿主或正式分发已经验收。

## 构建

在 macOS arm64 上准备 Python 3.11+、Node/npm，以及已构建的项目静态资源。从仓库根目录执行：

```sh
npm ci
npm run build
python3 desktop/electron/scripts/prepare-runtime.py
cd desktop/electron
npm ci
npm run dist
```

`npm ci` 是开发／构建时安装；项目 postinstall 显式安装 Electron 二进制。`npm run dist` 输出到 `desktop/electron/dist/`。Python 和 Node 随应用打包，应用首次启动不执行 pip、npm 或运行时下载。应用连接模型宿主时仍使用用户选择并实际安装／配置的宿主。

每次 `prepare-runtime.py` 都从**当前工作树**的 `src/`、`pyproject.toml` 和许可文件重新构建 BriefLoop wheel，再正常安装到新的 Python 目录。它不使用 editable 安装、不复用旧业务 wheel，不依赖开发者的 `.venv` 或绝对源码路径。更新或同步主分支后，重新运行准备脚本，再构建应用。若需要更新前端，先运行仓库根目录的 `npm run build`。

## 随包布局

构建目录 `desktop/electron/runtime/macos-arm64/` 整体作为 electron-builder `extraResources` 放入 `.app/Contents/Resources/runtime/`，不要放入 ASAR：

```text
runtime/
  python/bin/python3
  python/lib/python3.13/site-packages/briefloop/
  node/bin/node
  manifest.json
  licenses/
  relocation-proof.json
```

主进程通过 `python/bin/python3 -m briefloop ...` 启动后台，并把 `BRIEFLOOP_NODE` 指向 `node/bin/node`。路径必须从当前 `process.resourcesPath` 计算；开发态使用上述构建目录。Python 包已包含内联 WikiSkill、前端资源、模板与 `static/runtime-bridge.mjs`。Node 仅保留运行所需可执行文件与官方 LICENSE，省去 npm、corepack 和开发头文件。

## 固定来源和许可证

`scripts/runtime-lock.json` 固定如下归档及所有安装依赖 wheel 的 URL、版本与 SHA-256：

- CPython 3.13.15，python-build-standalone `20260901` 的 `aarch64-apple-darwin-install_only_stripped`。
- 同版 `pgo+lto-full` 归档，仅提取 `PYTHON.json` 与发行版附带的所有 `licenses/`。精简安装包本身不足以代替原生依赖许可目录。
- 官方 Node.js 22.23.2 `darwin-arm64` 发行包。
- 构建和业务依赖的 40 个 wheel，安装使用 `--no-index --require-hashes`。

构建下载缓存位于 `.cache/`；缓存仍逐次验证哈希，哈希不匹配拒绝使用。TLS 验证保持开启，脚本通过系统 `curl` 下载。运行时许可位于 `licenses/`，Python 包原有 dist-info、许可证和 RECORD 同时保留。缺少安装依赖的许可文件会使构建失败。BriefLoop wheel 自身许可证和前端第三方声明也随包保留。

来源与格式说明：[python-build-standalone 官方运行文档](https://gregoryszorc.com/docs/python-build-standalone/main/running.html)、[固定 Python 发行版](https://github.com/astral-sh/python-build-standalone/releases/tag/20260901)、[Node 官方固定校验表](https://nodejs.org/download/release/v22.23.2/SHASUMS256.txt)。更新锁文件属于显式构建维护：核对上游校验来源、重新解析目标 Python 的完整 wheel 依赖并记录下载哈希，再重新准备和验证；普通构建不会自动追随 latest。`pyproject.toml` 依赖变化但锁文件未更新时，构建明确失败。

## 搬迁验证与范围

准备脚本会自动调用：

```sh
python3 desktop/electron/scripts/verify-runtime.py desktop/electron/runtime/macos-arm64
```

检查把运行时临时移到与仓库无关、含空格的临时目录，去掉 `PYTHONPATH`、`PYTHONHOME` 和 `NODE_PATH`，限制 PATH 为系统工具目录，然后实际运行：

- 搬迁后的 Python 导入 BriefLoop、WikiSkill、MCP 和原生依赖；检查 wheel 内的 bridge 和前端许可资源。
- 搬迁后的 Node，核对 `arm64` 和实际执行路径。
- 搬迁后的 `briefloop --version` 以及 `python -m pip check`。

结果写入 `relocation-proof.json`。生成目录、下载缓存、应用输出不进 Git。该检查证明运行时可搬迁；签名、公证、Gatekeeper 和完整 `.app` 窗口／服务生命周期需要桌面产物验收单独记录。未经签名／公证的本地构建不能标成已正式分发。

## 窗口保存与退出协议

preload 提供 `briefloopDesktop.onPrepareClose(callback)`。main 为每次准备关闭生成随机请求 ID；renderer 通过现有 `savedVersion()`／保存队列完成保存后，只回传 `{status:'saved', version_id:string|null}`，失败则回传 `{status:'failed', error:string}`。失败或 30 秒内未收到确认时保留窗口，不把关闭窗口当保存成功。

保存确认后，main 才调用现有 `service-status` 盘点全部忙碌任务。用户选择继续工作时取消退出；选择停止并退出时使用 `service-stop` 的 `busy_action: 'cancel'`，随后等待自己启动的后台子进程实际退出。窗口和后台的完成状态分别确认。

`openWorkspace({path, create})` 是受限 IPC：main 校验调用窗口、当前 mainFrame 来源和本地路径，不接受命令、URL 或 PID。文件导入沿用同一 Web UI 的原生 file input；`will-download` 弹出系统另存对话框。可安装 `.app`／DMG 的最终位置和窗口操作结果以桌面验收记录为准。

保存握手开始时 renderer 暂设 `body.inert`，防止保存确认与关闭之间继续输入。preload 的固定 `onResume` 回调响应 `workspace:resume`；main 在取消关闭、保存失败或仅完成 Cmd+S 保存时发送它恢复编辑，失败路径也解除 inert。

原生运行时刚验证通过、仅业务源码变化时，可使用 `python3 desktop/electron/scripts/refresh-app.py`。它复用已锁定的 Python／Node，在临时副本中重建并强制重装当前 wheel，保留既有原生搬迁证明；从含同名 `briefloop.py` 的临时工作区运行 `-I -m briefloop --version`，再执行 `pip check`，成功后才替换产物。`app-refresh-proof.json` 记录新 wheel 哈希、Git HEAD 和时间，不把它写成重新执行了全部原生搬迁检查。锁文件或项目依赖变化时应使用完整准备流程。
