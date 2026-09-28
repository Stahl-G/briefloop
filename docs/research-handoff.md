# 研究交接与修订复核

研究结果仍复用现有 Scout、主 Agent、Analyst、Evaluator 和 Reviewer。程序记录来源、版本和检查状态，不替模型判断新闻重要性或证据能否支持业务推论。

## 当前缺口与历史

`join-scouts` / `research_status` 返回 `gap_records` 的稳定 `gap_id`。旧 `gaps: string[]` 继续兼容；未明确更新的项仍为 open。仅写 covered 或不再列入 open_questions 不会关闭缺口。

主 Agent 回读原文后，可在已有 `finish_research_round` 的 `gap_updates`，或 Native `save_research_handoff` 的 handoff 中提交同一字段：

```json
{
  "gap_updates": [{
    "gap_id": "使用回执中的实际ID",
    "status": "partial",
    "reason": "已取得同版官方公告，但尚未确认生效时刻",
    "remaining_question": "公告中的计划何时生效？",
    "evidence": [{"source_id": "实际来源ID", "locator": "line 3-5", "excerpt": "所定位位置的连续逐字原文"}]
  }]
}
```

partial/resolved 需要可定位的来源依据；open 可用具体 reason 重新开放。校验仅确认来源归属、位置、摘录与快照哈希，不代表独立事实核查。partial 的剩余问题进入当前视图，resolved 的理由与依据留在 `gap_history`；原始 Scout 工件不改写。依据快照变化或无法定位时，当前视图重新提示核对。

Native 在更新后自动刷新研究包。兼容宿主在收轮后再次 `join-scouts`，将刷新后的文件交给 Analyst；其写稿和修订包也使用同一当前视图。不要直接编辑数据库或原始 Scout 文件来消除提示。

写作计划是组织建议，不是事实来源；计划与冻结原文在时间、主体或阶段上冲突时，事实表述服从原文，并在研究记录中说明。用户明确的读者目的和写作要求仍保留。研究收尾优先补查会改变核心判断的材料，不能把检索失败说成来源不存在。

## 评价与修订复核

普通评分和独立 Reviewer 都取得已保存父版本及其评价发现。父评价只是待核对的观察，不是事实真值；须允许有依据地反驳原发现。

`assessment.checks` 复用现有数组；新记录含 `id / name / status / reason`。status 可为 passed、needs_attention、not_checked、disputed。缺少某项结果仅显示未核对，不新增模型回合，也不自动改写评分、独立审阅状态或正式交付结果。

`findings[].check_ids` 可选关联明确检查。仍存在的发现与 passed 检查明确相连时，检查显示 needs_attention，并保留模型原状态和一致性说明。旧记录仍可读；程序不按文字关键词猜关联。

每条修订复核需要同时核对问题原段、摘要、标题、表格与影响建议。前端在现有反馈区显示这些检查以及原发现说明。轻微问题可以与可用稿共存，评价检查记录也不等于全篇事实已核实。

新闻性和期间处理见 [报告时间范围](report-time-window.md)。本轮没有增加自动修订次数、额外工具入口或新的默认硬门。
