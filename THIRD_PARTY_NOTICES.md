# 来源与许可

- 原 BriefLoop（multi-agent-brief-workflow，920e2eef）：MIT。复用纸白/深绿/系统字体设计取值及来源、版本管理经验；未复制旧控制状态机。许可见 LICENSE。
- WikiSkill（Stahl-G/wikiskill，9df975b）：MIT。0.18.0 起作为顶层 `wikiskill` 包内联在本发行版（源码取自上游补丁版，非 wheel 依赖），随包携带完整许可与 NOTICE，见 `src/wikiskill/_licenses/`。上游源码在独立 WikiSkill 仓库维护。
- Tiptap / ProseMirror 及前端依赖由 package-lock.json 固定。`npm run build` 根据 esbuild 的实际产物输入汇总内联依赖的许可原文与 NOTICE，写入 `src/briefloop/static/frontend-licenses.txt`，随 wheel / sdist 分发。生成器为 `scripts/build_frontend_licenses.mjs`，按实际输入位置识别包（包含嵌套版本），缺失许可会令构建失败。各包的名称、版本与许可标识以生成清单为准；构建工具自身未进入产物时不列入。

- DeepSeek Harness（本地参考版本 c389f96bf3，MIT）：参考其侧栏、输入区与设置的交互组织，BriefLoop 界面独立实现，未引入其 Cordis 运行时。
- Tavily 官方 Agent Skills 与 API 文档（https://github.com/tavily-ai/skills，MIT）：作为 Search/Extract 用法参考。随包提供的 Tavily 技能为 BriefLoop CLI 适配版本；没有复制或要求安装完整官方技能仓库。

- pypdfium2 / PDFium：用于本地 PDF 页面渲染，通过 Python 依赖安装。pypdfium2 包声明 BSD-3-Clause、Apache-2.0 及其依赖许可，完整许可随依赖包保留。

- pi（https://github.com/earendil-works/pi，`@earendil-works/pi-coding-agent` 0.85.1）：MIT。内置引擎 `native-engine/` 通过其 SDK 创建受限审阅会话，未修改上游源码。`node native-engine/build.mjs` 按 esbuild 实际输入汇总内联依赖的许可原文，写入 `src/briefloop/static/native-engine-licenses.txt`，随 wheel / sdist 分发；pi 的 npm 包未附许可文件，其 MIT 原文取自上游仓库，见 `native-engine/third_party/pi/LICENSE`。

- OpenDesign（be0887b39273993d939e83c4768922115f104bed）：Apache-2.0。复用原生运行时协议和模型目录辅助代码，来源与本地改动见 third_party/open-design/NOTICE.md；许可证同时随打包后的 runtime bridge 分发。

### Runtime identification icons

Runtime SVG/PNG assets (listed in the NOTICE) come unmodified from [Open Design](https://github.com/nexu-io/open-design/tree/be0887b39273993d939e83c4768922115f104bed/apps/web/public/agent-icons), Apache-2.0; see `third_party/open-design/LICENSE` and `NOTICE.md`.
`static/runtime-codebuddy.svg` is the unmodified site icon from [CodeBuddy](https://www.codebuddy.cn), retrieved 2026-09-13 from https://download.codebuddy.cn/web/website/93a7cd0d70556625552d16b09c6a9cf8c2e089b9/assets/logo.svg. Brand artwork belongs to its respective owner and is used only to identify the corresponding runtime.
