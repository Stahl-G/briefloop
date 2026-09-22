"""Whether the chosen backend can run the restricted independent Reviewer (#726).

The declaration lives in backends.CAPABILITIES. Report creation, queued jobs,
schedules, scoring and the delivery gate all ask this module, so an unsupported
combination is explained before the first model call instead of after a draft.

- A fact check needs the Reviewer to admit its candidates: without it the paid
  searches cannot reach the report, so the task does not start.
- An internal report can still be written and scored. The score is recorded as
  ordinary assessment, never shown as an independent review, and formal
  delivery stays blocked until a supported backend completes the review.

The Reviewer may run on its own backend (settings.review_runtime, frozen into
each job as review_runtime). Checks therefore take the main backend together
with that choice; review_route() is the single place that resolves them.
"""
from .backends import BACKENDS, BACKEND_LABELS, REVIEW_ONLY_BACKENDS, supports, validate_backend

CODE = 'review_backend_unsupported'


class ReviewBackendUnsupported(ValueError):
    code = CODE


def restricted_review(backend):
    return supports(validate_backend(backend), 'restricted_review')


def review_route(backend, review_runtime=None):
    """(backend, runtime) the Reviewer runs on, or None when no route exists.

    runtime is None when the Reviewer follows the main chain, which then keeps
    using its own Evaluator model configuration."""
    if review_runtime:
        from .models import ReviewRuntime, runtime_fields
        chosen = ReviewRuntime.model_validate(review_runtime)
        if not restricted_review(chosen.backend):
            return None
        return chosen.backend, runtime_fields(chosen.model_dump(exclude_none=True), chosen.backend)
    if restricted_review(backend):
        return validate_backend(backend), None
    return None


def review_available(backend, review_runtime=None):
    return review_route(backend, review_runtime) is not None


def review_choices():
    """Every backend the Reviewer can be pinned to, including review-only ones."""
    return [{'id': name, 'label': BACKEND_LABELS[name], 'experimental': name in REVIEW_ONLY_BACKENDS}
            for name in BACKENDS if supports(name, 'restricted_review')]


def review_backends():
    """Backends a user can switch the main chain to and still get the Reviewer.

    A review-only engine may run a pinned review job, but suggesting it as the
    “执行后端” to change to would point at a choice the page does not offer."""
    return [name for name in BACKENDS
            if supports(name, 'restricted_review') and name not in REVIEW_ONLY_BACKENDS]


def summary():
    """Shown to the page, which keeps no list of its own."""
    return {'restricted_review': [{'id': name, 'label': BACKEND_LABELS[name]} for name in review_backends()],
            'review_choices': review_choices()}


def _alternatives():
    return '、'.join(BACKEND_LABELS[name] for name in review_backends()) or '暂无已验证的执行后端'


def _remedy():
    return f'可在设置的“独立审阅执行后端”中单独选择 {"、".join(c["label"] for c in review_choices())}，或把执行后端改为 {_alternatives()}'


def _label(backend):
    return BACKEND_LABELS.get(backend, str(backend))


def require_for_fact_check(backend, review_runtime=None):
    if not review_available(backend, review_runtime):
        raise ReviewBackendUnsupported(
            f'{_label(backend)} 尚未验证受限独立审阅，联网事实核查的结果必须由独立审阅复核，所以没有开始。'
            f'{_remedy()}，或关闭事实核查后生成。')


def require_for_review(backend):
    if not restricted_review(backend):
        raise ReviewBackendUnsupported(
            f'{_label(backend)} 尚未验证受限独立审阅，不能运行独立审阅，也不会退回普通写权限。'
            f'可改用 {_alternatives()} 后重新审阅；已有稿件、编辑和普通下载不受影响。')


def delivery_blocker(backend, review_runtime=None):
    """None when the configured route could complete the missing review."""
    if review_available(backend, review_runtime):
        return None
    return {'code': CODE, 'message': f'当前执行后端 {_label(backend)} 尚未验证受限独立审阅；'
                                     f'正式交付需要先完成独立审阅。{_remedy()}。'}
