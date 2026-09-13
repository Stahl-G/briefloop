# macOS 签名与公证

正式消除“无法验证开发者”提示，需要 Apple 签发的 Developer ID Application 证书（含私钥）和 Apple 公证通过。临时签名不能替代它们。当前已发资产是否签名，以该资产的验证记录为准；新增构建入口不会追溯改变已下载的 DMG。

## 一次配置

1. 使用有 Developer ID 权限的 Apple Developer Program 账号，在 Xcode/Apple 开发者门户申请 Developer ID Application 证书，并将证书与私钥放入构建 Mac 的钥匙串。
2. 使用 `xcrun notarytool store-credentials briefloop-notary` 的交互提示，将公证认证保存到本机钥匙串。支持的账号或 API 凭据按 Apple 官方说明配置，不写进 Git、聊天、脚本或发行日志。
3. 指定证书名称（省略 `Developer ID Application: ` 前缀）和钥匙串 profile 名。以下值只是示例，需要使用实际账号：

```sh
export BRIEFLOOP_APPLE_SIGNING_IDENTITY='Your Name (TEAMID)'
export APPLE_KEYCHAIN_PROFILE='briefloop-notary'
cd desktop/electron
npm run signing:check
```

`signing:check` 检查本机证书和公证认证，不构建或上传 App。自定义钥匙串可设置 `APPLE_KEYCHAIN`。此入口统一使用 profile，不同时设置其他 Apple 认证环境变量。

## 每次发行

按仓库统一发行流程冻结源码并准备与 Python/Windows 一致的 backend wheel；完成版本、wheel 哈希与前端检查后执行：

```sh
npm run dist:signed
```

该命令强制签名、启用 Hardened Runtime，由 electron-builder 签名、公证并 staple App，再生成 DMG/ZIP。随后签名验证 DMG、提交 DMG 公证并 staple，运行 `codesign`、`stapler validate`、`spctl` 检查。缺证书、认证失败、公证未接受或验证失败时退出，不静默降级为未签名包。不会自动发布。

`dist/mac-signing.json` 保存本次版本、源码提交、wheel 哈希、DMG 公证提交 ID 与最终文件哈希。公证会把安装包提交给 Apple 检查。最终仍需从下载所得安装包进行原生安装、启动和升级验收；常规首次打开确认与无法验证开发者警告不是同一回事。

`npm run dist` 保留未签名本地构建路径。未取得正式验证结果前，README/网站中的未签名说明必须保留。签名后的安装包继续使用现有 DMG 更新方式；不同时更改更新协议。

参考：[Apple Developer ID](https://developer.apple.com/developer-id/)、[Apple 公证说明](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)、[Electron notarize](https://github.com/electron/notarize)。
