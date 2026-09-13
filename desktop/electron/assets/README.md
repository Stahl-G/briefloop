# BriefLoop 应用图标

本目录使用用户提供的 `BriefLoop-app-icons.zip`，采用主款 **#006838 绿底、#FAF9F6 纸白回环**。这是本次 macOS／Windows 的统一来源；未引入其他品牌资产，也未重绘或转换平台图标。压缩包没有附带 README、构建说明或工具版本；本说明记录导入与检查事实，不补造上游生成过程。

| 用途 | 本目录文件 | 原压缩包内路径 |
| --- | --- | --- |
| macOS 应用图标 | `Mac.icns` | `BriefLoop-app-icons/BriefLoop.icns` |
| Windows 应用图标 | `Win.ico` | `BriefLoop-app-icons/BriefLoop.ico` |
| Mac SVG 母版 | `icon-macos.svg` | `BriefLoop-app-icons/icon-macos.svg` |
| Windows SVG 母版 | `icon-windows.svg` | `BriefLoop-app-icons/icon-windows.svg` |
| Mac 1024 PNG | `icon-macos-1024.png` | `BriefLoop-app-icons/icon-1024.png` |
| Windows 1024 PNG | `icon-windows-1024.png` | `BriefLoop-app-icons/png/windows-1024.png` |

所有二进制、PNG 与 SVG 均逐字节提取。`source-info.json` 保存压缩包哈希、完整清单、所选文件的原路径／哈希及格式检查，不含开发者私人绝对路径。

## 格式检查

- 两份 SVG：1024 × 1024，viewBox 为 `0 0 1024 1024`，主色与回环色符合上述指定。
- 两份 PNG：1024 × 1024，带透明区域。
- ICNS：8 个表示层，覆盖 32、64、128、256、512、1024 实际像素尺寸；最大层与所附 Mac 1024 PNG 像素一致。
- ICO：16、24、32、48、64、128、256 像素共 7 层；256 层与所附 Windows 256 PNG 像素一致。较小层与压缩包单独提供的 PNG 并非逐像素相同；本次保留原 ICO，不重建其图层。

未提取浅纸底备选款 `icon-macos-paper.svg`、`png/paper-*` 或本次不需要的网页 favicon／其他重复 PNG。两平台轮廓与留白沿用压缩包各自的母版。

构建配置从 `desktop/electron/package.json` 所在目录引用 `assets/Mac.icns` 和 `assets/Win.ico`；PNG 与 SVG 留作预览和后续明确授权的资产维护。本目录不提供自动转换脚本，Windows 构建直接使用 `Win.ico`。
