"""Admission for independent standard review and packet-confined strict review.

Both use the same version/evidence/findings validation. Mode describes execution
isolation, not whether an accepted semantic review is valid for delivery.
"""
from .backends import BACKENDS, BACKEND_LABELS, REVIEW_ONLY_BACKENDS, supports, validate_backend
from .opencode_version import installed_major as _opencode_major

CODE = 'review_backend_unsupported'


class ReviewBackendUnsupported(ValueError):
    code = CODE


def normalize_mode(mode):
    if mode not in ('standard', 'strict'):
        raise ValueError('审阅模式必须为 standard 或 strict')
    return mode


def restricted_review(backend):
    """Strict isolation must be verified, not inferred from a major version."""
    return supports(validate_backend(backend), 'restricted_review')


def standard_review(backend):
    backend = validate_backend(backend)
    return supports(backend, 'standard_review') and (backend != 'opencode' or _opencode_major() in (1, 2))


def supports_review(backend, review_mode='standard'):
    return restricted_review(backend) if normalize_mode(review_mode) == 'strict' else standard_review(backend)


def review_route(backend, review_runtime=None, review_mode='standard'):
    """Resolve only the requested route; never replace an unsupported choice."""
    normalize_mode(review_mode)
    if review_runtime:
        from .models import ReviewRuntime, runtime_fields
        chosen = ReviewRuntime.model_validate(review_runtime)
        if not supports_review(chosen.backend, review_mode):
            return None
        return chosen.backend, runtime_fields(chosen.model_dump(exclude_none=True), chosen.backend)
    if supports_review(backend, review_mode):
        return validate_backend(backend), None
    return None


def review_available(backend, review_runtime=None, review_mode='standard'):
    return review_route(backend, review_runtime, review_mode) is not None


def review_choices(review_mode='standard'):
    return [{'id': name, 'label': BACKEND_LABELS[name], 'experimental': name in REVIEW_ONLY_BACKENDS,
             'review_modes': [mode for mode in ('standard', 'strict') if supports_review(name, mode)]}
            for name in BACKENDS if supports_review(name, review_mode)]


def review_backends(review_mode='standard'):
    return [name for name in BACKENDS if supports_review(name, review_mode) and name not in REVIEW_ONLY_BACKENDS]


def summary():
    choices = review_choices()
    strict = [{'id': c['id'], 'label': c['label']} for c in review_choices('strict')]
    return {'restricted_review': strict, 'strict_review': strict,
            'standard_review': [{'id': c['id'], 'label': c['label']} for c in choices],
            'review_modes': ['standard', 'strict'], 'review_choices': choices,
            'unavailable_reviewers': [{'id': name, 'label': BACKEND_LABELS[name],
                'review_mode': mode, 'reason': _limitation(name, mode)}
                for name in ('codex', 'opencode', 'briefloop-native') for mode in ('standard', 'strict')
                if not supports_review(name, mode)]}


def _remedy(mode):
    names = '、'.join(c['label'] for c in review_choices(mode)) or '暂无已验证的执行后端'
    return f'请在设置的“独立审阅执行后端”中选择支持该模式的 {names}；不会自动更换模式或执行后端'


def _limitation(backend, mode='standard'):
    label = BACKEND_LABELS.get(backend, str(backend))
    if mode == 'strict':
        return f'{label} 尚未验证核查包隔离，不能执行严格审阅；可显式改选普通独立审阅，或选择支持严格审阅的执行后端'
    if backend == 'opencode':
        return '未检测到受支持的 OpenCode 1.x / 2.x，无法执行普通独立审阅'
    return f'{label} 尚未接入实际只读的独立审阅通道'


def require_for_fact_check(backend, review_runtime=None, review_mode='standard'):
    if not review_available(backend, review_runtime, review_mode):
        reviewer = (review_runtime or {}).get('backend', backend)
        raise ReviewBackendUnsupported(f'{_limitation(reviewer, review_mode)}。联网事实核查的结果仍需独立审阅，所以没有开始。{_remedy(review_mode)}。')


def require_for_review(backend, review_mode='standard'):
    if not supports_review(backend, review_mode):
        raise ReviewBackendUnsupported(f'{_limitation(backend, review_mode)}。{_remedy(review_mode)}；已有稿件、编辑和普通下载不受影响。')


def delivery_blocker(backend, review_runtime=None, review_mode='standard'):
    if review_available(backend, review_runtime, review_mode):
        return None
    reviewer = (review_runtime or {}).get('backend', backend)
    return {'code': CODE, 'message': f'{_limitation(reviewer, review_mode)}；本版本仍需完成独立审阅。{_remedy(review_mode)}。'}
