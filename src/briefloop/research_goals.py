"""Question-level research decisions over the existing frozen plan/handoff.

These are author judgments with inspectable evidence, never independent
verification. No scheduler, model call or second mutable task store lives here.
"""
from .models import ResearchGapEvidence


def contract(requirements):
    questions = list(dict.fromkeys(q.strip() for q in requirements.get('key_questions', []) if q.strip()))
    if not questions:
        questions = [requirements.get('objective', '').strip() or requirements['title']]
    return {'version': 1, 'questions': [{'id': f'q{index}', 'question': text}
            for index, text in enumerate(questions, 1)]}


def validate(store, run_id, raw, *, plan=None, require_complete=False):
    plan = plan if plan is not None else (store.meta('research_plan:' + run_id) or {})
    goal = plan.get('goal_contract')
    if not goal:
        return None  # Historical frozen runs do not acquire new obligations.
    if raw is None:
        if require_complete:
            raise ValueError('收轮前请在 handoff.json 的 question_coverage 中逐项交代 goal_contract.questions；未知可保留 open，不要求额外检索。')
        return None
    if not isinstance(raw, list):
        raise ValueError('question_coverage 必须为数组')
    known = {q['id']: q for q in goal['questions']}
    seen, result = set(), []
    from .research_handoff import _proof
    for value in raw:
        if not isinstance(value, dict):
            raise ValueError('question_coverage 每项必须为对象')
        identity = value.get('question_id')
        if not isinstance(identity, str) or identity not in known or identity in seen:
            raise ValueError('question_id 必须来自本任务 goal_contract 且不得重复')
        seen.add(identity)
        status = value.get('status')
        if status not in ('answered', 'partial', 'open'):
            raise ValueError('问题状态必须为 answered、partial 或 open')
        reason, remaining = value.get('reason'), value.get('remaining_question', '')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('每个问题需要 reason，简述依据或未解决原因')
        if not isinstance(remaining, str) or (status != 'answered' and not remaining.strip()):
            raise ValueError('open/partial 需要 remaining_question；不能省略未答问题')
        if status == 'answered' and remaining.strip():
            raise ValueError('仍有 remaining_question 时应标记 partial')
        evidence = value.get('evidence', [])
        if not isinstance(evidence, list) or (status != 'open' and not evidence):
            raise ValueError('answered/partial 至少需要一条可定位 evidence')
        checked = [_proof(store, run_id, ResearchGapEvidence.model_validate(item)) for item in evidence]
        result.append({'question_id': identity, 'status': status, 'reason': reason.strip(),
                       'remaining_question': remaining.strip(), 'evidence': checked})
    if require_complete and seen != set(known):
        raise ValueError('question_coverage 尚未交代：' + '、'.join(sorted(set(known) - seen)) + '；可标记 open 并说明未答范围')
    return result


def view(plan):
    if not plan or not plan.get('goal_contract'):
        return None
    latest = {}
    for info in sorted(plan.get('rounds', {}).values(), key=lambda r: r.get('index', 0)):
        if info.get('status') == 'closed':
            for item in (info.get('outcome') or {}).get('question_coverage') or []:
                latest[item['question_id']] = item
    return {'scope': '主 Agent 的问题覆盖判断；有依据不等于独立核实通过。',
            'questions': [{**question, 'recorded': question['id'] in latest, **latest.get(question['id'], {'status': 'open', 'reason': '尚未提交覆盖判断',
                'remaining_question': question['question'], 'evidence': []})} for question in plan['goal_contract']['questions']]}
