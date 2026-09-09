# 来源与许可

- 原 BriefLoop（multi-agent-brief-workflow，920e2eef）：MIT。复用纸白/深绿/系统字体设计取值及来源、版本管理经验；未复制旧控制状态机。许可见 LICENSE。
- WikiSkill（Stahl-G/wikiskill，9df975b）：MIT，源码在独立 WikiSkill 仓库维护；BriefLoop 的 vendor/wheels 提供其已构建 wheel 并自动安装，用户无需第二次 clone。版本 0.1.1+briefloop.2，完整许可包含在 wheel metadata。
- Tiptap / ProseMirror 及前端依赖由 package-lock.json 固定。各自许可保留在包中；esbuild 输出许可文件一并打包。

- DeepSeek Harness（本地参考版本 c389f96bf3，MIT）：参考其侧栏、输入区与设置的交互组织，BriefLoop 界面独立实现，未引入其 Cordis 运行时。
- Tavily 官方 Agent Skills 与 API 文档（https://github.com/tavily-ai/skills，MIT）：作为 Search/Extract 用法参考。随包提供的 Tavily 技能为 BriefLoop CLI 适配版本；没有复制或要求安装完整官方技能仓库。

- pypdfium2 / PDFium：用于本地 PDF 页面渲染，通过 Python 依赖安装。pypdfium2 包声明 BSD-3-Clause、Apache-2.0 及其依赖许可，完整许可随依赖包保留。
