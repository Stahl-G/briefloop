"""What a learning run may spend, and whether it was authorized (#727).

Saving feedback and using approved skills never start model calls. Starting a
learning validation does, so every entry — the page, the agent tool, the idle
worker and a resumed batch — passes through the same authorization record here.
The bound is computed from the limits the learner enforces; prices are unknown
to BriefLoop and never guessed.
"""
import hashlib
import json

from .backends import BACKEND_LABELS

# learning._experience keeps the three most recent eligible reports.
MAX_CASES = 3
# Baseline and candidate trial per case; a reusable baseline is not rewritten.
TRIALS_PER_CASE = 2
# One logical Evaluator invocation classifies a batch's new revisions. Host
# internal turns and token use are not bounded by this invocation count.
TRIAGE_TURNS_PER_BATCH = 1
# An explicit human requirement allows one repair round (learning.learn).
EXPLICIT_REQUIREMENT_ROUNDS = 2
AUTHORIZATION_CODE = 'learning_authorization_required'
SCOPE_FIELDS = ('cases', 'backend', 'model', 'role_models', 'trial_generations_per_round',
                'other_turns_per_round', 'triage_turns_per_batch')


class LearningAuthorizationRequired(ValueError):
    code = AUTHORIZATION_CODE


def max_rounds(k):
    return max(int(k), EXPLICIT_REQUIREMENT_ROUNDS)


def plan(settings):
    k = int(settings['k'])
    rounds = max_rounds(k)
    backend = settings.get('agent_backend', 'codex')
    # Role overrides bill separately, so the confirmation must name them too.
    roles = {role: {key: value for key, value in (config or {}).items() if key in ('model', 'model_provider', 'model_variant', 'reasoning_effort')}
             for role, config in (settings.get('role_models') or {}).items()}
    value = {
        'cases': MAX_CASES,
        'rounds': k,
        'rounds_with_explicit_requirement': rounds,
        'trial_generations_per_round': MAX_CASES * TRIALS_PER_CASE,
        'max_trial_generations': MAX_CASES * TRIALS_PER_CASE * rounds,
        # Each round also runs the maintainer and proposer turns and one pairwise comparison.
        'other_turns_per_round': 3,
        # One logical Evaluator invocation per batch classifies new revisions (#858).
        'triage_turns_per_batch': TRIAGE_TURNS_PER_BATCH,
        'web': False,
        'backend': backend,
        'backend_label': BACKEND_LABELS.get(backend, backend),
        'model': settings.get('model') or '',
        'role_models': roles,
        # A trial is one logical generation; a host may run its own sub-agents and
        # token use is not bounded by this number.
        'counts': 'trial_generations',
        'price': 'unknown',
    }
    value['fingerprint'] = _sha(value)
    # The automatic record authorizes rounds up to the confirmed number, so its
    # scope covers who runs the work, not how many rounds were chosen.
    value['scope_fingerprint'] = _sha({key: value[key] for key in SCOPE_FIELDS})
    return value


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def state(settings):
    """off, needs_confirmation, rounds_exceed, plan_changed, or authorized."""
    if not settings.get('auto_learn'):
        return 'off'
    authorized = settings.get('auto_learn_authorized_rounds')
    if authorized is None:
        return 'needs_confirmation'
    if int(settings['k']) > authorized:
        return 'rounds_exceed'
    if not settings.get('auto_learn_authorized_plan'):
        return 'needs_confirmation'
    if settings.get('auto_learn_authorized_plan') != plan(settings)['scope_fingerprint']:
        return 'plan_changed'
    return 'authorized'


def snapshot(settings):
    return {'state': state(settings), 'authorized_rounds': settings.get('auto_learn_authorized_rounds'), 'plan': plan(settings)}


def automatic_allowed(settings):
    return state(settings) == 'authorized'


def authorization(settings, kind, *, confirmed=None):
    """The record frozen into a learning batch; kind is 'automatic' or 'manual'.

    A manual start must confirm the exact plan the user was shown: a settings
    change between the confirmation and the queue does not silently apply.
    """
    current = plan(settings)
    if kind == 'manual':
        if not isinstance(confirmed, str) or confirmed != current['fingerprint']:
            raise LearningAuthorizationRequired('学习设置在确认之后发生了变化；请重新查看调用上限并确认')
    elif kind == 'automatic':
        if not automatic_allowed(settings):
            raise LearningAuthorizationRequired('自动学习尚未获得确认；请先在设置中确认调用上限')
    else:
        raise ValueError('Unknown learning authorization kind')
    return {'kind': kind, 'rounds': current['rounds'], 'fingerprint': current['fingerprint'],
            'max_trial_generations': current['max_trial_generations'],
            'triage_turns_per_batch': current['triage_turns_per_batch']}


def verify(record, budget=None):
    """Execution-time check, including the additional revision-triage allowance.

    Pre-triage frozen records do not cover this invocation. Keep their feedback
    and completed work, and require a new confirmation before resuming.
    """
    if (not isinstance(record, dict) or record.get('kind') not in ('automatic', 'manual') or not record.get('fingerprint')
            or type(record.get('triage_turns_per_batch')) is not int
            or record['triage_turns_per_batch'] != TRIAGE_TURNS_PER_BATCH):
        raise LearningAuthorizationRequired('这批学习没有包含改动分类调用的可核验额度确认记录（可能是升级前排队的）；'
                                            '请在设置中确认调用上限后重新发起，反馈和已完成的试写都保留')
    if budget is not None:
        # Compare with the frozen plan, never today's settings. A new record may
        # not be attached to an older plan that omitted the triage invocation.
        valid = (isinstance(budget, dict) and type(budget.get('triage_turns_per_batch')) is int
                 and budget['triage_turns_per_batch'] == record['triage_turns_per_batch']
                 and budget.get('fingerprint') == record['fingerprint']
                 and budget.get('rounds') == record.get('rounds')
                 and budget.get('max_trial_generations') == record.get('max_trial_generations'))
        if valid:
            fingerprint = _sha({key: value for key, value in budget.items() if key not in ('fingerprint', 'scope_fingerprint')})
            scope = _sha({key: budget.get(key) for key in SCOPE_FIELDS})
            valid = budget['fingerprint'] == fingerprint and budget.get('scope_fingerprint') == scope
        if not valid:
            raise LearningAuthorizationRequired('这批学习的冻结调用预算与确认记录不一致或未包含改动分类调用；'
                                                '请重新查看调用上限并确认，反馈和已完成的试写都保留')
    return record


def apply_settings_change(current, body):
    """Merge a settings request; only an explicit confirmation records authorization."""
    body = dict(body)
    for key in ('auto_learn_authorized_rounds', 'auto_learn_authorized_plan'):
        body.pop(key, None)
    confirm = body.pop('confirm_learning_rounds', None)
    confirm_plan = body.pop('confirm_plan', None)
    merged = {**current, **body}
    if 'auto_learn' in body and not body['auto_learn']:
        merged['auto_learn_authorized_rounds'] = None
        merged['auto_learn_authorized_plan'] = None
    elif merged.get('auto_learn'):
        k = int(merged['k'])
        if confirm is not None or confirm_plan is not None:
            if type(confirm) is not int or confirm != k:
                raise LearningAuthorizationRequired('确认的学习轮数与当前设置不一致，请重新查看上限后确认')
            expected = plan(merged)['fingerprint']
            if confirm_plan is not None and confirm_plan != expected:
                raise LearningAuthorizationRequired('学习设置在确认之后发生了变化；请重新查看调用上限并确认')
            merged['auto_learn_authorized_rounds'] = k
            merged['auto_learn_authorized_plan'] = plan(merged)['scope_fingerprint']
        elif body.get('auto_learn') and not current.get('auto_learn'):
            raise LearningAuthorizationRequired('开启自动学习会在后台调用模型做验证；请先确认调用上限')
    return merged
