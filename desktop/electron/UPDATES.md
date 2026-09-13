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
| `installMode` | `native` 或 `dmg` |
| `error` | null 或 `{code,message}`，不含凭据或原始错误全文 |
| `retryable` | 操作失败后是否可重试 |
| `source` | `github` 或 `local-test`；后者应在界面持续明确标注 |

下载失败可再次调用 `download()`；源信息失效或缺少资产时先 `check()`。不允许降级或预发布。检查/下载不自动安装，也不自动停止服务。

## 当前 macOS 路径

当前 App 未签名/公证，且尚未建立公开的桌面更新 feed，因此默认 `installMode: 'dmg'`。Electron 官方要求 macOS App 签名后才能自动更新；此路径只查询发布、下载并打开安装镜像，不声称原地升级。[Electron autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater)

通过 GitHub `releases/latest` 查询稳定版本，仍显式拒绝 draft、prerelease 和非稳定 SemVer。必须有唯一 Apple Silicon DMG，正式文件名为 `BriefLoop-{稳定版本}-arm64.dmg`；下载 URL 必须属于返回的精确 release tag，且末段文件名一致。缺资产或版本不匹配时明确拒绝下载。GitHub 资产字段包括下载 URL、size 和可选 digest。[GitHub Releases API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)

下载起点限于官方仓库 `releases/download/` 地址；只允许其返回的 GitHub release-assets CDN HTTPS 重定向。临时文件在 `userData/updates` 内，完整下载后核对声明大小及存在的 SHA-256/SHA-512 digest，fsync 后原子重命名；失败清除本次临时目录。没有上游 digest 时只验证大小、保留本地 SHA-256 用于安装前防篡改检查，**不声称取得了上游哈希证明**。下载后的安装门禁调用 `shell.openPath()` 打开本模块生成的本地 DMG；用户自行完成安装。若打开 DMG 暂时失败，可保留已下载资产重试；每次仍重走保存/忙任务门禁并再次校验。检查与 `openPath` 之间仍存在同用户替换文件的时序窗口，本实现不宣称阻止恶意同用户本地篡改。

## 原生更新路径

Windows 默认委托 `electron-updater`；未来签名 macOS 构建可由主进程显式配置 `installMode: 'native'`。固定 GitHub provider，设置 `autoDownload=false`、`autoInstallOnAppQuit=false`、`allowPrerelease=false`、`allowDowngrade=false`。本模块不写 installer，不更换应用文件；实际安装只调用 `quitAndInstall(false, true)`。

原生更新仍需要正式 installer、对应 `latest.yml`/`latest-mac.yml` 等产物与平台签名配置。macOS 还需要 ZIP 配合更新元数据；NSIS 的安装/签名配置由 Windows 工作负责。`quitAndInstall()` 会先关闭窗口，主进程必须在调用前完成保存、busy 决策和 owned 服务退出，并避免再次触发同一退出门禁。[electron-builder 更新指南](https://www.electron.build/docs/features/auto-update/)、[AppUpdater API](https://www.electron.build/docs/api/electron-updater.class.appupdater/)

## 无公开发布的本地验证

构造器允许主进程显式传入 `testFeed: 'http://127.0.0.1:PORT/release'`，仅适用于 DMG 测试路径；也可用 localhost 或 IPv6 回环。该地址返回 GitHub Release 形状 JSON。测试资产必须在同一回环 origin，重定向不能离开；DTO 强制 `source: 'local-test'`。本模块不自动读取环境变量。主进程仅在 `!app.isPackaged` 时接受显式 `BRIEFLOOP_UPDATE_TEST_FEED`；打包 App 忽略该变量，始终使用官方来源。界面持续显示“本地测试更新源”，不接受网页传入 feed。

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
    "browser_download_url": "http://127.0.0.1:PORT/asset",
    "digest": "sha256:实际资产的64位十六进制摘要"
  }]
}
```

运行 `node --test test/updater.test.cjs`。测试使用临时回环 HTTP 服务和合成字节，不执行安装器：覆盖失败重试、哈希拒绝、稳定版/降级/缺资产、越界 URL 拒绝、公开来源约束、原生库方法委托及自动安装关闭。它们不证明 GitHub 公开更新链、macOS 原地更新或 Windows 本机安装已验收；安装包实测应由各平台另行完成。
