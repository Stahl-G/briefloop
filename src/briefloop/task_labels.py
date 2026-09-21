"""One name per task kind, for every surface that shows one.

The same task used to be called several different things depending on where it
appeared: the task list, the progress card, the status note in the
conversation and the session title each kept their own table, so `assess` was
"重新评分" in one place, "独立评分" in another and "核对简报评分" in a third.
A kind is named here and nowhere else.

Membership is a separate decision from naming: REPORTED is the set a user sees
as a task of their own, and surfaces that only show those filter on it rather
than on whether someone remembered to add a label.
"""

LABELS = {
    'generate': '生成简报',
    'assess': '重新评分',
    'review': '独立审阅',
    'revise': '按审阅修订',
    'fact_check': '独立事实核查',
    'learn': 'WikiSkill 学习',
    'export_docx': '生成工作稿 Word',
    'release': '制作正式 Word',
    'audit_bundle': '制作审计包',
    'source_refresh': '复查来源',
    'prepare_template': '准备模板',
    # A staged step inside a generation session, not a queued task of its own.
    'company_review': '维护企业背景',
}

STEPS = ('company_review',)
REPORTED = tuple(kind for kind in LABELS if kind not in STEPS)


def label(kind, default='报告任务'):
    return LABELS.get(kind, default)


def reported_labels():
    """What the page needs: the kinds it lists, with their one name each."""
    return {kind: LABELS[kind] for kind in REPORTED}
