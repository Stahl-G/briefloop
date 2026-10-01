# BriefLoop v3.1 应用资产

主色来自 tokens.css 的黛蓝，回环使用冷灰纸白。runtime-briefloop.svg 为本次用户提供的原始 SVG（保留元数据），桌面图标使用同一回环路径，沿用原 macOS / Windows 轮廓和留白。

运行 python3 scripts/build_brand_assets.py 重建两平台 SVG、1024 PNG、Mac.icns、Win.ico 和 DMG 背景。需要 Inkscape 与 Pillow。ICNS 覆盖至 1024 像素；ICO 含 16/24/32/48/64/128/256 像素。source-info.json 记录本次来源和派生文件哈希，不再声称派生文件与旧素材包逐字节一致。

DMG 背景为 540×380，应用图标与 Applications 位于 (150,210) 和 (390,210)。Linux 上检查资产格式与像素；macOS Finder 中的最终安装窗口仍需原生打包验收。
