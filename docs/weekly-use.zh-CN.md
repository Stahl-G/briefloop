# 我每周怎么用 MABW

这不是信任地图，也不是控制面说明。它只是一份习惯脚本：每周怎么把 MABW 用起来。

## 七句话版本

周一，`/mabw run`，让它把一周攒下的资料跑成可审计草稿，我去开会。

周二，读草稿，把不顺眼的地方原话丢给 `/mabw feedback`，两分钟。

周三，看它拆出来的反馈：值得长期记住的点头，该核查的让它核查，该变模板的不要写进 memory。

周四，重跑一次，专门看上周批准的偏好这次有没有进入 snapshot；有疑问就 `/mabw status`。

周五，`/mabw deliver`，门禁全绿，只交 `output/delivery/brief.md` 和 `output/delivery/<命名周报>.docx`。来源附录留作审计追溯，不单独当成交付文件。

周六，不碰它。

周日，不碰它。它替我记着进度、来源、偏好和把关记录，我才能真的休息。

## 每周只记住这四件事

| 你关心的事 | 用哪个入口 |
|---|---|
| 本期写到哪了 | `/mabw status <workspace>` |
| 每个数字哪来的 | 看 Claim Ledger、source appendix、audit/gate 结果 |
| 它学到了什么 | 看 Improvement Ledger；只有批准过的偏好才会生效 |
| 什么在替你把关 | 看 gate、reader-clean 和 finalize-complete 结果 |

## 两个习惯

第一，不把事实问题说成偏好。数字错了、来源缺了、日期过期了，走本期核查和修复。

第二，不把固定格式长期写成 memory。每期都必须有的结构，应该升级成模板、规则或交付标准。
