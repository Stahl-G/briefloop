# 来源与许可

- 原 BriefLoop（multi-agent-brief-workflow，920e2eef）：MIT。复用纸白/深绿/系统字体设计取值及来源、版本管理经验；未复制旧控制状态机。许可见 LICENSE。
- WikiSkill（Stahl-G/wikiskill，9df975b）：MIT，源码在独立 WikiSkill 仓库维护；BriefLoop 的 vendor/wheels 提供其已构建 wheel 并自动安装，用户无需第二次 clone。版本 0.1.1+briefloop.2，完整许可包含在 wheel metadata。
- Tiptap / ProseMirror 及前端依赖由 package-lock.json 固定。各自许可保留在包中；esbuild 输出许可文件一并打包。
