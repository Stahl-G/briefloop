"""Read-only, packet-time research context; never rewrite a saved report's gaps."""
import json

from .research_handoff import gap_view
from .store import content_hash, dump

GUIDE = '''若任务包提供 research_context（审阅包为 research-context.json），区分两个时间层：saved_report 是本稿保存时的缺口记录，current_research 是这次任务包生成时已登记的研究状态。两者同时保留，不用后来补到的材料反推当时已经获得，也不把旧“未取得”自动当作当前仍未取得。
当前状态 resolved 只表示研究 Agent 已提交带定位依据的解决判断，不是独立事实认证；回读对应来源并检查依据是否真正回答该问题。未登记更新、无法对应的旧稿缺口仍需核对，不能因 current_research.gaps 为空就宣布全部补齐；依据失效时保留重新开放状态。此上下文不会修改正文、历史评分或审阅状态。缺少该字段的旧任务包按其实际保存内容核查，不补造当前状态。
纠正旧缺口时同时检查摘要、正文、表格及影响建议中是否保留了同一过时结论。取得公告不等于公告中的计划已经实现：保留公测/正式可用、拟议/生效、最高/已取得、预测/实际及其主体期间；融资不等于客户付费。正确保留这些条件的表述不应因缺少机器绑定被误判为错误。'''


def snapshot(store, brief):
    detail = brief.get('detail') or {}
    if isinstance(detail, str):
        detail = json.loads(detail)
    current = gap_view(store, brief['run_id'])
    return {
        'version_id': brief['id'], 'brief_hash': brief['hash'], 'research_state_hash': content_hash(dump(current)),
        'saved_report': {'gaps': detail.get('gaps', []), 'gap_records': detail.get('gap_records', [])},
        'current_research': current,
        'scope': '本次任务包生成时的研究记录；不是历史可得性证明、事实认证或自动关闭报告缺口。',
    }
