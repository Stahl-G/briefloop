# App 更新接口与验收边界

本模块更新桌面 App。版本取 `app.getVersion()`，不读取 Python 后端版本、模型版本或工作区数据。生产来源固定为 `Stahl-G/briefloop` 的公开 GitHub Releases；不接收 renderer 提供的 URL、feed、命令或本地安装路径。浏览器设置只显示网页客户端版本、官方下载入口与桌面安装提示；桌面设置中的当前 App 版本由主进程提供。

## 主进程接入

生产依赖：`electron-updater@6.8.9`、`semver@7.8.5`。不要把 updater 暴露为任意方法的 IPC 代理。

```js
const {createUpdater} = require('./updater.cjs');
const updates = createUpdater({app, shell, changed: dto => sendUpdateState(dto)});
// 只为这三个无参数操作提供受信任的 IPC：
updates.status();
await updates.check();
await updates.download();
// installReady 不直接绑定 renderer：先完成主进程已有保存/忙碌确认/owned 服务停服门禁。
await updates.installReady();
```

`status()` 返回独立副本；`changed(dto)` 在状态或进度变化时通知。`check()` 和 `download()` 返回更新后的 DTO，失败包含可显示的固定错误消息，不抛出原始网络诊断。二者执行中重复操作共用当前 Promise。`installReady()` 失败会抛出脱敏错误，并更新 DTO；下载前调用会拒绝。

DTO 字段：

| 字段 | 含义 |
| --- | --- |
| `currentAppVersion` | 当前 App 版本 |
| `state` | `idle/checking/available/current/downloading/downloaded/error` |
| `releaseVersion` | 已检查的稳定发布版本；尚未检查为 null |
| `notes` | 发布说明字符串；必须按文本显示，不能直接作为 HTML 插入 |
| `url` | 官方 Release 页面；本地测试时为测试地址 |
| `progress` | null 或 `{percent,transferred,total}`，字节为单位 |
| `installMode` | `native`、`zip` 或 `dmg` |
| `error` | null 或 `{code,message}`，不含凭据或原始错误全文 |
| `retryable` | 操作失败后是否可重试 |
| `reinstall` | 仅显式本地源的同版本 DMG 为 true；界面显示“重新安装当前 App”，不称为新版本 |
| `source` | `github` 或 `local-test`；后者应在界面持续明确标注 |

下载失败可再次调用 `download()`；源信息失效或缺少资产时先 `check()`。不允许降级或预发布。检查/下载不自动安装，也不自动停止服务。

## 当前 macOS 路径

macOS 当前默认使用 `installMode: 'zip'`：下载、校验、解压后打开 Finder，由用户替换 App；首次安装仍用 DMG。当前发行未做 Developer ID 签名或 Apple 公证，不声称原地自动升级。显式 `dmg` 模式仍可打开已验证的 DMG。

更新检查只读取固定的 `https://github.com/Stahl-G/briefloop/releases/latest/download/release-manifest.json`，不调用匿名 GitHub REST API，也不读取用户 GitHub 凭据。此清单从 0.26.1 开始随正式工件发布，含稳定版本、冻结源码提交、各工件字节数及 SHA-256。清单缺失时显示明确错误，不回退到可能已限流的 REST 接口。

逐跳检查 HTTPS 重定向：官方 latest 别名先解析为同仓库精确 tag 下的清单，再允许 GitHub release-assets CDN；清单中的版本必须与此 tag 完全一致。清单不能指定其他下载域名。每次成功检查缓存五分钟，连续点击复用结果；过期后重新请求，失败不缓存为成功。若静态文件服务自身返回 429 或明确的配额耗尽响应，仍按 Retry-After/reset 暂停请求。没有自动轮询检查。

必须有唯一的 Apple Silicon 更新包，名称为 `BriefLoop-{稳定版本}-arm64-mac.zip`（DMG 模式为 `BriefLoop-{稳定版本}-arm64.dmg`），并绑定到清单的精确 tag。生产清单每个工件都必须有大小和 SHA-256，ZIP 不接受缺少上游校验值。无新版本时只报告当前版本，不允许降级或预发布。

下载起点限于官方仓库 `releases/download/` 地址；只允许其返回的 GitHub release-assets CDN HTTPS 重定向。临时文件在 `userData/updates` 内，完整下载后核对声明大小及 SHA-256/SHA-512 digest，fsync 后原子重命名；失败清除本次临时目录。ZIP 经解压及 App 标识/版本核对后交给 Finder；DMG 模式打开本模块生成的本地 DMG。安装前仍经过保存、忙任务和退出门禁，再次校验下载文件。此复核不宣称消除恶意同用户并发替换文件的全部时序窗口。

每次发行必须用最终工件生成并上传 `release-manifest.json`，不能在签名或修改包之前计算哈希。生成脚本只读取工件，不重建它们；已发布版本不可用不同内容覆盖。发行验收包含一次公开 latest 清单读取、版本及包哈希比对，再进行各平台实际安装验收。

```sh
node desktop/electron/scripts/prepare-release-manifest.cjs --dist /path/to/frozen-artifacts --commit FROZEN_40_CHARACTER_SHA
```

版本自动取 `pyproject.toml`；可选 `--version` 必须与其一致。目录中只放本次最终安装包、同一 wheel/sdist、更新索引和验收记录；混入旧版本安装包会失败。脚本不会覆盖不同内容的既有清单。清单与其中列出的所有工件一起上传到对应正式 release，再检查 `releases/latest/download/release-manifest.json` 可用。

## 原生更新路径

Windows 默认委托 `electron-updater`；未来签名 macOS 构建可由主进程显式配置 `installMode: 'native'`。固定 GitHub provider，设置 `autoDownload=false`、`autoInstallOnAppQuit=false`、`allowPrerelease=false`、`allowDowngrade=false`。本模块不写 installer，不更换应用文件；实际安装只调用 `quitAndInstall(false, true)`。

Windows 下载完成后，以当前实例的公开 `update-downloaded` 元数据和 `downloadUpdate()` 返回路径固定目标版本、上游 SHA-512 与实际 EXE 路径。文件名须为 `BriefLoop-Setup-{版本}-{架构}.exe`；缺校验信息、路径不一致、多 EXE 或额外 web-installer 包文件会拒绝，并提示重新检查下载。安装门禁内再次核对实际文件及 SHA-512；不将本地重新计算的 hash 当作可信基线。此复核不保证消除恶意同用户并发替换的全部时序窗口，也不解析 EXE 内部版本。

安装请求失败后，Windows 清除 ready 并废弃本次 NsisUpdater；下次显式下载通过新实例重新核对同一目标版本和缓存。仅移除本包装层的进度/下载监听，旧实例保留无副作用的 error 监听；其迟到事件不能改变新实例状态。主进程按已接受安装请求的顺序保留退出事务，旧失败实例排队的退出仍被阻止；同步拒绝及安装前校验失败不会占用退出事务队列。不修改更新器的私有安装标志。

原生更新仍需要正式 installer、对应 `latest.yml`/`latest-mac.yml` 等产物与平台签名配置。macOS 还需要 ZIP 配合更新元数据；NSIS 的安装/签名配置由 Windows 工作负责。`quitAndInstall()` 会先关闭窗口，主进程必须在调用前完成保存、busy 决策和 owned 服务退出，并避免再次触发同一退出门禁。[electron-builder 更新指南](https://www.electron.build/docs/features/auto-update/)、[AppUpdater API](https://www.electron.build/docs/api/electron-updater.class.appupdater/)

## 无公开发布的本地验证

构造器允许主进程显式传入 `testFeed: 'http://127.0.0.1:PORT/release'`，适用于 ZIP/DMG 测试路径；也可用 localhost 或 IPv6 回环。该地址返回 GitHub Release 形状 JSON。测试资产必须在同一回环 origin，路径必须为 `/Stahl-G/briefloop/releases/download/{tag_name}/BriefLoop-{version}-arm64.dmg`，重定向不能离开；DTO 强制 `source: 'local-test'`。本模块不自动读取环境变量。主进程仅在 `!app.isPackaged` 时接受显式 `BRIEFLOOP_UPDATE_TEST_FEED`；打包 App 忽略该变量，始终使用官方来源。界面持续显示“本地测试更新源”，不接受网页传入 feed。

```json
{
  "tag_name": "v0.20.0",
  "draft": false,
  "prerelease": false,
  "html_url": "http://127.0.0.1:PORT/notes",
  "body": "本地更新流程测试，不是公开发布",
  "assets": [{
    "name": "BriefLoop-0.20.0-arm64.dmg",
    "size": 123,
    "browser_download_url": "http://127.0.0.1:PORT/Stahl-G/briefloop/releases/download/v0.20.0/BriefLoop-0.20.0-arm64.dmg",
    "digest": "sha256:实际资产的64位十六进制摘要"
  }]
}
```

运行 `node --test test/updater.test.cjs`。测试使用临时回环 HTTP 服务和合成字节，不执行安装器：覆盖失败重试、哈希拒绝、稳定版/降级/缺资产、越界 URL 拒绝、公开来源约束、原生库方法委托及自动安装关闭。它们不证明 GitHub 公开更新链、macOS 原地更新或 Windows 本机安装已验收；安装包实测应由各平台另行完成。

`node --test test/updater.test.cjs test/updater-integration.test.cjs` 另使用实际 NsisUpdater/BaseUpdater 的缓存、完成事件和退出调度，只替换传输、spawn、App 与工作区服务。合成字节放在中文/空格临时路径；验证异步启动失败后的第二次 spawn、旧事件/旧退出与新请求交错、同步失败及文件变更时恢复会话。这仍是隔离行为验证，不运行安装器，不证明系统安装失败后旧程序完整回滚。

显式本地源也可提供与当前 App 完全相同的稳定版本，用于重新安装真实构建。此时 `reinstall: true`，界面显示“重新安装当前 App v{版本}”；版本、tag、资产文件名、大小与校验要求保持一致，不能用更高的虚报版本包装旧构建。本地 feed 作者须核对 DMG 内 App 的实际版本；下载器不挂载 DMG 或自行解释安装包，单凭文件名与哈希不证明包内 App 版本。生产官方源对同版本仍返回 `current`，所有来源均不能下载降级版本。此能力不改变正式更新 feed，也不代表发布了新版本。

原生 `installReady()` 的 `requested: true` 仅表示请求已交给成熟更新器，不表示安装成功。主进程在请求返回后继续监听该安装事务的错误；在 App 仍存活时收到失败，会撤销退出、恢复 owned 服务并解除编辑器暂停。`electron.autoUpdater` 的 `before-quit-for-update` 用于识别更新器随后排队的退出，失败事务会阻止它且不会重新进入用户退出门禁。这里不以短延时推断安装完成；进程已经退出后的安装器失败仍由平台安装器处理，本地测试不构成 Windows 安装成功证明。

## 增量下载（ZIP / DMG / Windows NSIS）

- macOS 默认使用 ZIP/Finder 替换流程，保留显式 DMG 路径；增量只减少传输，不修改正在运行的 App。
  发布最终 ZIP/DMG 和同名 `.blockmap` 到同一 release；静态清单必须提供完整包的
  SHA-256。`npm run dist` 生成并验证 blockmap；签名流程在 stapling
  改变 DMG 后重新生成，不能上传 stapling 前的旧 map。
- 第一次没有缓存时下载全包并保存已校验的基线。后续版本使用 electron-builder 的
  blockmap 比较数据块，只用 HTTP Range 下载变化部分，再验证整个重建 DMG。
  缓存损坏、map 不可用、Range 不受支持、变化超过 90%、过多请求或校验失败时回退
  全量。新基线保存成功后清理前一个已验证基线，不扫描/删除用户的其他下载。
- Windows NSIS 显式开启 `differentialPackage` 和 electron-updater 的差分下载。
  发布 EXE、对应 `.exe.blockmap` 和 `latest.yml`，保留旧版本资产。使用原生安装器
  管理的 `installer.exe` / `current.blockmap` 缓存；缓存缺失或差分失败时原生更新器
  回退完整 EXE。不能因存在 blockmap 就声称某次更新实际使用了增量。
- 两端都下载并验证完整可安装产物后才进入既有保存/忙任务/退出门禁。
  Electron 升级或大量内容变化仍可能下载接近全包。旧版更新器需先全量升级一次。

本地行为验证：`node --test test/updater.test.cjs test/differential-update.test.cjs`。
真实 DMG 对比（不安装、不改变已安装 App）：
`node scripts/verify-differential-download.cjs /path/old.dmg /path/new.dmg`。
输出实际安装包请求字节（不含小型 metadata/blockmap 和 HTTP 头）、重建哈希与节省比例。
Windows 应额外记录真实 NSIS 差分传输与安装后版本；Mac 的下载重建测试不能冒充 Windows 安装验收。
