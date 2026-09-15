"""What a learning run may spend, and whether automatic learning was authorized (#727).

Saving feedback and using approved skills never start model calls. Starting a
learning validation does, so automatic learning needs an explicit, recorded
confirmation of the upper bound shown here. The bound is computed from the same
limits the learner enforces; prices are unknown to BriefLoop and never guessed.
"""
from .backends import BACKEND_LABELS

# learning._experience keeps the three most recent eligible reports.
MAX_CASES = 3
# Baseline and candidate trial per case; a reusable baseline is not rewritten.
TRIALS_PER_CASE = 2
# An explicit human requirement allows one repair round (learning.learn).
EXPLICIT_REQUIREMENT_ROUNDS = 2
AUTHORIZATION_CODE = 'learning_authorization_required'


class LearningAuthorizationRequired(ValueError):
    code = AUTHORIZATION_CODE


def max_rounds(k):
    return max(int(k), EXPLICIT_REQUIREMENT_ROUNDS)


def plan(settings):
    k = int(settings['k'])
    rounds = max_rounds(k)
    return {
        'cases': MAX_CASES,
        'rounds': k,
        'rounds_with_explicit_requirement': rounds,
        'trial_generations_per_round': MAX_CASES * TRIALS_PER_CASE,
        'max_trial_generations': MAX_CASES * TRIALS_PER_CASE * rounds,
        # Each round also runs the maintainer and proposer turns and one pairwise comparison.
        'other_turns_per_round': 3,
        'web': False,
        'backend': settings.get('agent_backend', 'codex'),
        'backend_label': BACKEND_LABELS.get(settings.get('agent_backend', 'codex'), settings.get('agent_backend', 'codex')),
        'model': settings.get('model') or '',
        'price': 'unknown',
    }


def state(settings):
    """off, needs_confirmation (never confirmed, e.g. after upgrade), rounds_exceed, or authorized."""
    if not settings.get('auto_learn'):
        return 'off'
    authorized = settings.get('auto_learn_authorized_rounds')
    if authorized is None:
        return 'needs_confirmation'
    if int(settings['k']) > authorized:
        return 'rounds_exceed'
    return 'authorized'


def snapshot(settings):
    return {'state': state(settings), 'authorized_rounds': settings.get('auto_learn_authorized_rounds'), 'plan': plan(settings)}


def automatic_allowed(settings):
    return state(settings) == 'authorized'


def apply_settings_change(current, body):
    """Merge a settings request; only an explicit confirmation records authorization."""
    body = dict(body)
    body.pop('auto_learn_authorized_rounds', None)
    confirm = body.pop('confirm_learning_rounds', None)
    merged = {**current, **body}
    if 'auto_learn' in body and not body['auto_learn']:
        merged['auto_learn_authorized_rounds'] = None
    elif merged.get('auto_learn'):
        k = int(merged['k'])
        if confirm is not None:
            if type(confirm) is not int or confirm != k:
                raise LearningAuthorizationRequired('确认的学习轮数与当前设置不一致，请重新查看上限后确认')
            merged['auto_learn_authorized_rounds'] = k
        elif body.get('auto_learn') and not current.get('auto_learn'):
            raise LearningAuthorizationRequired('开启自动学习会在后台调用模型做验证；请先确认调用上限')
    return merged
