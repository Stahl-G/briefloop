# App 专属 Python 环境

桌面 App 不内置完整 Python。`environment.cjs` 检测本机 Python 3.11 或更高版本；未找到时明确引导安装 Python，检测本身不安装软件。用户触发准备后，使用随包 wheel 的声明依赖创建 App 专属 venv，不修改系统 Python 或工作区的环境。

## 主进程接口

```js
const {createEnvironment} = require('./environment.cjs');
const environment = createEnvironment({
  app,
  payloadPath: path.join(process.resourcesPath, 'backend'),
  changed: dto => sendEnvironmentStatus(dto),
});
await environment.inspect();  // 检测和验证；不创建 venv、不联网安装
await environment.prepare();  // 只接用户明确选择“准备环境”的无参数 IPC
const runtime = environment.runtime(); // 仅验证 ready 后返回，否则抛错
await environment.cancel();   // 等待本模块自己的准备进程退出并清理未完成目录
```

`status()` 返回独立 DTO 副本：

- `state`: `checking`、`missing-python`、`needs-setup`、`installing`、`ready`、`error`。
- `phase`: `idle`、`verify-payload`、`detect-python`、`needs-setup`、`create-venv`、`install-dependencies`、`verify-imports`、`verify-dependencies`、`activate-environment`、`ready`。
- `pythonVersion`: 检测到的 Python 版本或 null。
- `error`: null 或 `{code,message}`，只有固定安全提示，不复制子进程原始输出、路径、环境变量或网络诊断。
- `retryable`: 是否可重新检测或准备。

没有模拟百分比。重复调用时复用当前操作 Promise；启动检查尚未结束时，界面应禁用准备按钮。`runtime()` 只返回主进程可用的 `{python, node: process.execPath, nodeIsElectron: true}`，不得把这些路径当成 renderer 提供的命令。服务启动层应按约定以 Electron Node 模式启动桥接进程；本模块不修改其环境或启动业务服务。

主进程关闭门禁必须等待 `cancel()` 完成，不能先结束 App 再放任 pip 在后台写入。取消也适用于正在运行的检测；取消后可重试。若无法确认子树已退出，`cancel()` 会抛出安全错误，主进程必须保留 App；模块会保留未完成目录并禁止新准备，不能把这个状态当成已取消。POSIX 准备子进程使用独立进程组，先 SIGTERM、必要时 SIGKILL，并独立等待整个组退出，leader 先退出不会取消后续清理；Windows 用系统 taskkill 的记录 PID 与 `/T /F` 回收该子树。只处理本模块持有的进程，不扫描或停止工作区服务。

## 随包清单与检测

`payloadPath` 是主进程固定的绝对目录，包含 `manifest.json` 和指定 wheel：

```json
{"version":"0.19.0","wheel":"briefloop-0.19.0-py3-none-any.whl","sha256":"wheel 文件的 64 位 SHA-256"}
```

每次 `inspect()` 都复核 wheel 哈希、记录的基础 Python 仍可执行，以及 venv 的真实导入与依赖一致性。清单不接受路径穿越或 wheel 符号链接。已有环境损坏、版本/哈希不匹配时返回 `needs-setup`；缺基础 Python 时显示 `missing-python`。损坏的清单或 wheel 属于 App 安装问题，显示错误并提示重新安装 App。

宿主查找只使用 PATH 中的绝对目录和标准安装路径，包括 macOS Homebrew、Python.framework，及 Windows Python 安装目录、`py.exe -0p` launcher 清单。跳过相对 PATH 条目以及可能打开商店的 Python WindowsApps 别名。先列出已有解释器再直接探测，避免新版 Python install manager 在无解释器时自动安装；子进程也显式关闭 manager 自动安装并移除旧 launcher 安装开关。[Windows Python 文档](https://docs.python.org/3/using/windows.html)

launcher 返回的实际解释器路径必须可用，且 Python 版本满足要求；不依赖开发者源码路径，也不从 shell 配置执行命令。

## 准备与切换

环境位于 `app.getPath('userData')/environments/<UUID>`。创建后不改名，避免 venv 脚本的绝对 shebang 失效；这是 Python 官方注明的 venv 限制。[Python venv 文档](https://docs.python.org/3/library/venv.html)

准备过程固定执行：

1. `python -I -m venv <UUID 目录>`。
2. venv Python 的 `-I -m pip --isolated --disable-pip-version-check --no-input install --only-binary=:all: --index-url https://pypi.org/simple <随包 wheel>`。
3. 在隔离模式下导入 `briefloop`、`wikiskill`、`mcp`、`docx`、`lxml`、`PIL`、`pypdf`、`pypdfium2`、`openpyxl`，核对解释器确在目标 venv、安装的 BriefLoop 版本与清单一致。
4. 执行 `pip check`。全部通过后 fsync 并原子替换 `active.json`，才公开 `ready`。

进程不继承 PYTHON/PIP 配置变量、VIRTUAL_ENV 或 ELECTRON_RUN_AS_NODE。设置 `PIP_CONFIG_FILE` 为平台空设备，连全局 pip 配置也不读取；索引固定为 PyPI，不接受 renderer 指定 URL。只安装预编译 wheel，不因某个 Python 版本缺轮子而启动源码构建。[Python 隔离模式](https://docs.python.org/3/using/cmdline.html#cmdoption-I)、[pip install](https://pip.pypa.io/en/stable/cli/pip_install/)

Python 探测有 10 秒单进程上限，venv 创建 2 分钟，依赖安装 15 分钟，导入和 pip check 各 1 分钟。超时和进程失败显示对应阶段与可重试错误。失败或取消只删除本次未完成的 UUID 目录，不覆盖 `active.json` 或删除上一套环境；当前 App 的 `runtime()` 在未验证就绪时仍拒绝启动。旧环境的后续清理由单独管理流程决定。

## 验证边界

运行 `node --test test/environment.test.cjs`。测试覆盖只读检测、缺 Python、wheel 哈希错误、准备/验证/原子切换、失败重试、取消保留旧环境、真实子进程及忽略 TERM 的子孙进程回收、配置隔离与标准宿主路径。安装过程测试使用合成 payload 和确定性 runner，不把它写成真实依赖安装成功；本机实际准备和 Windows 子树回收仍须由对应平台验收。

`runProcess` 与 `candidates` 构造参数用于主进程单元测试依赖注入，不属于 IPC。公开到 renderer 的操作应固定为无参数 status/inspect/prepare/cancel，并复用已有可信窗口检查。模块不会自动安装系统 Python、启动服务、执行模型或改变工作区数据。
