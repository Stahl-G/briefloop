---
feature: home-v2
status: delivered
updated: 2026-09-18
branch: design/system-v2
commits: 4afde8358f4707fbd05ac8c23b9b794b003a5b97..7cd02001617167e1bd277c2ddc475dbb28863efe
---

# 首页按 DESIGN v2 改版

## Report

**What was built** — Homepage and shared composer brought in line with DESIGN v2: tokens.css served and linked; execution parameters moved into a params popover with default row 附件/模型/参数/发送; empty schedule/recent blocks gated without placeholder copy; primary sidebar CTA on 新建报告; suggestion icons use categorical colors; GENRE_META academic/securities hues no longer collide with primary/danger. Normal mode adds a right rail (#home-rail) with running jobs and recent reports; recent reports appear only there.

**Verification** — `npm run build` PASS; `npm test` 66 collected, 0 fail; `frontend_home_v2.mjs` PASS; Playwright: tokens.css 200; params panel ~222px stays open; `#new-report.primary`; empty schedule hidden; `#home-rail` visible at 340px with recent report, main recent hidden.

**Journey log** — Global document click closer also hides new popovers unless excluded; `.popover` subclasses must set `top:auto` when positioning with `bottom`. Server asset allowlist must include `/tokens.css` for both preload and GET branch. Home rail must toggle `has-home-rail` only on the home view and exclude recent from the main column when the rail is present.

## [S1] Problem
首页仍按 v1 呈现：composer 常驻十余执行参数、空工作区三段空状态叠放、侧栏「新对话/新建报告」同权重、建议卡无分类色标、未接入 `tokens.css`。违反 DESIGN §3.2/§5/§6/§7.2/§7.3。

## [S2] Design
范围限定**首页与共用 composer**（对话页共用 composer 一并受益）；不做报告工作区右栏、不做全站 stylelint。

1. **令牌接入**  
   - `index.html` 在 `style.css` 之前加载 `tokens.css`。  
   - 首页新样式只引用语义层（`--color-*`、`--space-*`、`--control-h-*`、`--cat-*`）。

2. **空态 / 常态（§3.2 §6.3 §7.3）**  
   - 空态：无运行任务 ∧ 无报告 ∧ 无定时计划 → 只显示问候语 + composer + 「你可以试试」；**不渲染**定时报告、最近的报告区块；侧栏会话列表空态只保留一句。  
   - 常态：定时报告 / 最近报告**仅在有数据时** `hidden=false`；禁止输出「还没有计划」「还没有报告」占位段。  
   - 「最近的报告」在**主区底部不作为常态内容**；有数据时进入右栏（S4），仅出现一次。

3. **Composer 渐进披露（§6.5 §7.2）**  
   - 默认一行：`＋附件` · 模型控件 · `⚙︎ 参数` · 发送（主键）+ 模式下拉（split：排队发送/立即补充，仅活跃回合可 steer）。  
   - 参数 popover 收纳：权限、已有来源、宿主、推理档位、速度档、Provider、研究档位/事实核查/自动学习/轮数/更多（既有 `compact-report-options`）。  
   - 控件高度 `--control-h-sm`，字号 `--text-xs`；停止钮保留 danger；窄屏可换行。  
   - **保留全部既有 `#id`**（§9.2 迁移期白名单）；仅新增按钮/容器可用 id 或 `data-testid`。

4. **主操作（§6.2）**  
   - `#new-report` 使用 `.primary`；`#new-session` 使用次级描边/幽灵样式。窄屏 560px 图标折叠行为保持可用。

5. **分类色标（§5）**  
   - 四张建议卡图标按类着色并换可区分字符/线性图标：周报/公司→`--cat-business-*`，竞品→`--cat-markets-*`，资料→`--cat-academic-*`；不铺大面积底。  
   - 同步 `GENRE_META`：学术 `#006838`→`#00695C`，证券 `#C62828`→`#AD1457`（与 tokens.css 一致）。  
   - 报告卡 `.report-card-icon` 本阶段按 genre/content method 映射同一 `--cat-*`；无 genre 用中性。

6. **问候与占位**  
   - 问候保持功能型文案；不额外加引导大按钮。  
   - 删除与 composer 重复的「生成第一份报告」类主 CTA（若 HTML/动态插入中存在）。

## [S3] Out of Scope
- stylelint 强制、`data-testid` 全量迁移、深色模式  
- 报告工作区/设置页/模板页改版  
- 后端 API 行为变更  
- `--content-max`/`--page-max` 整页超宽收敛  

## [S4] 首页常态右栏（Amendment 2026-09-18）
用户要求补上 DESIGN §3.2/§7.3 首页三区右栏。

1. **形态**  
   - 空态（无报告 ∧ 无定时 ∧ 无运行任务）：单列，**不渲染** `#home-rail`。  
   - 常态（任一有内容）：主列 + `--rail-width` 右栏；rail 内区块**仅在有内容时**显示。

2. **右栏内容**  
   - **运行中的任务**：`state.jobs` 中 `queued|running` 的简要列表（标题/状态/打开入口）；无则整块不渲染。  
   - **最近的报告**：`homeRecentReports()`；**只在右栏出现一次**（§6.2）。主区底部 `#home-block-recent` 在常态下隐藏。

3. **主区保留**  
   - 问候、composer、你可以试试、**定时报告**（有计划才显示）仍在主列。

4. **布局**  
   - `#chat` 增加主列 wrapper + `aside.rail#home-rail`；`has-rail` 时 grid `1fr var(--rail-width)`；≤1150px rail 下移为单列。

## Tasks
- [x] T1: 接入 tokens.css 并补充首页/composer/分类色 CSS — acceptance: `index.html` 引用 tokens.css；新增规则仅用语义令牌；`npm run build` 通过 (covers: S2.1)
- [x] T2: 空态/常态区块门控 — acceptance: 空工作区 DOM 中定时/最近报告区块 hidden，且无「还没有…」占位文案；有数据时区块显示 (covers: S2.2)
- [x] T3: Composer 参数 popover + 默认行 — acceptance: 默认可视控件 ≤4 类（附件/模型/参数/发送）；权限与 compact 选项在 popover 内；既有 `#chat-send` `#chat-model` 等 id 仍在 DOM；相关 frontend 测试通过 (covers: S2.3)
- [x] T4: 侧栏主次按钮 — acceptance: `#new-report` 为 primary，`#new-session` 非 primary；视觉权重可区分 (covers: S2.4)
- [x] T5: 分类色与 GENRE_META 同步 — acceptance: 四张建议卡使用不同 `--cat-*`；`GENRE_META` 含 `#00695C`/`#AD1457` 且无与 primary/danger 冲突的旧分类色；模板相关测试通过 (covers: S2.5)
- [x] T6: 首页常态右栏 — acceptance: 有报告/任务/定时时 `#home-rail` 可见且含运行任务或最近报告；最近报告不在主区底部重复；空态无 rail (covers: S4)
