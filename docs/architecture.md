# BriefLoop 架构

> 本图由代码调研绘制（2026-09-12），依据均指向仓库源码。BriefLoop 是本地研究与报告工具：主 Agent 负责对话、研究规划、写稿与修订，Evaluator / Wiki Maintainer / Skill Proposer 独立运行，所有结论绑定证据版本。

## 整体架构图

```mermaid
flowchart TB
    subgraph 浏览器
        UI["Web UI<br/>(static/index.html + app.js)<br/>原生 HTML/CSS/JS，无构建框架"]
    end

    subgraph Python 服务 ["Python 服务 (src/briefloop)"]
        SRV["HTTP 服务 server.py:21<br/>ThreadingHTTPServer :8765<br/>GET /api/state · POST /api/*"]
        CLI["CLI cli.py<br/>serve/start/tool/…"]
        WK["Worker runtime.py:334<br/>任务编排、提示词生成<br/>(generation_prompt:125, assessment_prompt:278)"]
        IR["InteractiveRuntime interactive_runtime.py:56<br/>会话式对话执行"]
        ST["Store store.py:88<br/>SQLite ControlStore：来源/稿件版本/<br/>评分绑定/事件/冲突恢复"]
        MD["models.py pydantic 模型<br/>Settings/Requirements/BriefDraft/…"]
        SRC["sources.py / tavily.py / evidence.py<br/>来源抓取、Tavily 检索、证据绑定"]
        RV["review.py:1<br/>独立只读核对<br/>评分与冲突检测 conflicts.py"]
        DL["delivery_checks.py / release.py /<br/>document_export.py / audit_bundle.py<br/>交付检查、正式发布、审计包导出"]
        LRN["learning.py:1<br/>反馈学习：经验提取、<br/>试错对比、入 Wiki"]
    end

    subgraph 执行引擎层 ["执行引擎 Harness 层"]
        HM["HarnessManager harness.py:16<br/>(codex CLI 宿主)"]
        OH["OpencodeHarness opencode_harness.py<br/>+ backends/opencode_server.py"]
        BH["BridgeHarness bridge_harness.py<br/>(claude / kimi / hermes 等)"]
    end

    subgraph Node 桥 ["Node 运行时桥"]
        RB["RuntimeBridge runtime_bridge.py:12<br/>按需 spawn Node 进程"]
        TS["runtime-bridge.mjs<br/>(源码 runtime-bridge/main.ts)<br/>ACP 协议胶水，宿主/模型发现"]
    end

    subgraph 模型宿主 ["外部 CLI 宿主"]
        CODEX["codex CLI"]
        OC["opencode server"]
        ACP["claude / kimi / hermes /<br/>kilo / kiro / vibe (ACP)"]
    end

    subgraph 学习与技能 ["WikiSkill (src/wikiskill)"]
        ENG["engine.py 事件账本驱动的<br/>验证门控进化引擎"]
        WAG["wiki_agents.py<br/>Wiki Maintainer / Skill Proposer"]
    end

    CLI --> SRV
    UI -- "fetch /api/* + token (frontend/app.js:40)" --> SRV
    SRV --> ST
    SRV --> WK
    WK --> IR
    IR -- pick_harness (server.py:43) --> HM
    IR --> OH
    IR --> BH
    BH --> RB
    RB --> TS
    TS -- ACP --> ACP
    HM --> CODEX
    OH --> OC
    WK --> SRC
    WK --> ST
    WK --> RV
    RV --> ST
    RV --> DL
    WK --> LRN
    LRN --> ST
    LRN --> ENG
    ENG --> WAG
    MD --- ST
```

## 分层说明

1. **界面层**：界面源码在 `frontend/`（app.js、rich-document.js），由 esbuild 打包（`npm run build`，`package.json`）输出到 `src/briefloop/static/app.js`，Python 服务只发布 `static/`（原生无框架运行时 + 打包后的 bundle，含 tiptap 富文档依赖）。浏览器用带 `X-BriefLoop-Token` 的 JSON 请求访问 `/api/*`，token 过期时自动重新握手（`frontend/app.js:40` 的 `api()`）。

2. **服务层**：`cli.py` 的 `serve/start` 进入 `server.py:make_server()`。服务以工作区为单位单实例运行（`fcntl` 锁，`server.py:28`），启动时组装 Store、各 Harness 管理器和 `Worker`，并一次性恢复中断的会话状态（`harness.chat.recover_stale()`）。`GET /api/state` 返回整个工作区快照，写操作走 `POST /api/<命令>`。

3. **执行层**：`Worker`（`runtime.py:334`）负责把任务分阶段（`stage_job`）、按角色生成提示词并调度；`InteractiveRuntime` 把每个会话请求路由到 `pick_harness()` 选择的引擎，且同一会话锁定原宿主（切换引擎须新建会话，`server.py:45`）。引擎有三种宿主方式：codex CLI（`HarnessManager`）、opencode server（`OpencodeHarness`）、以及通过 Node 桥的 ACP 宿主（claude/kimi/hermes 等，`BridgeHarness` → `RuntimeBridge` spawn `static/runtime-bridge.mjs`，源码在 `runtime-bridge/main.ts`，复用 `third_party/open-design` 的 Apache 协议辅助代码）。

4. **数据层**：`Store`（`store.py:88`）是精简 SQLite ControlStore，保存来源（含 Tavily 抓取与 PDF/图片 sidecar，`sources.py`/`tavily.py`/`media.py`）、稿件版本、证据绑定（`evidence.py`）、评分与事件流；pydantic 模型集中在 `models.py`。低分稿件不隐藏，工作稿始终可下载。

5. **核验与交付层**：`review.py` 做独立只读核对（评审模型收到打包的稿件、证据与本任务历史，不重跑分析），冲突由 `conflicts.py` 记录；`delivery_checks.py`/`release.py` 把正式交付与证据版本绑定，`audit_bundle.py` 按用户选择导出审计包（不含凭据与隐藏推理）。

6. **学习层**：`learning.py` 把报告反馈排入队列，提取经验、生成试错对比（`_generate_trial`），再把获胜修订交给 `src/wikiskill/engine.py`——一个以追加式事件账本为准、带验证门控的技能进化引擎，由 Wiki Maintainer / Skill Proposer（`officeqa/wiki_agents.py`）维护既有 WikiSkill。

## 边界事实

- Python 只负责工具、进程、存储、确定计算与既定选择规则；规划/研究/写作/评分都由 CLI 宿主里的实际 agent 完成（AGENTS.md 约定）。
- 一个工作区同时只允许一个服务实例（`server.py:28` 的 `fcntl` 锁）。
- runtime-bridge 需要本机 Node 20+，构建产物为 `src/briefloop/static/runtime-bridge.mjs`（`runtime-bridge/build.mjs`）。
- 后端可选：`codex/opencode/claude/kimi/hermes/reasonix/mimo`（`cli.py:21`），ACP 类宿主经 Node 桥。
