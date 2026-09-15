"""Whether the chosen backend can run the restricted independent Reviewer (#726).

The declaration lives in backends.CAPABILITIES. Report creation, queued jobs,
schedules, scoring and the delivery gate all ask this module, so an unsupported
combination is explained before the first model call instead of after a draft.

- A fact check needs the Reviewer to admit its candidates: without it the paid
  searches cannot reach the report, so the task does not start.
- An internal report can still be written and scored. The score is recorded as
  ordinary assessment, never shown as an independent review, and formal
  delivery stays blocked until a supported backend completes the review.
"""
from .backends import BACKENDS, BACKEND_LABELS, supports, validate_backend

CODE = 'review_backend_unsupported'


class ReviewBackendUnsupported(ValueError):
    code = CODE


def restricted_review(backend):
    return supports(validate_backend(backend), 'restricted_review')


def review_backends():
    return [name for name in BACKENDS if supports(name, 'restricted_review')]


def summary():
    """Shown to the page, which keeps no list of its own."""
    return {'restricted_review': [{'id': name, 'label': BACKEND_LABELS[name]} for name in review_backends()]}


def _alternatives():
    return '、'.join(BACKEND_LABELS[name] for name in review_backends()) or '暂无已验证的执行后端'


def _label(backend):
    return BACKEND_LABELS.get(backend, str(backend))


def require_for_fact_check(backend):
    if not restricted_review(backend):
        raise ReviewBackendUnsupported(
            f'{_label(backend)} 尚未验证受限独立审阅，联网事实核查的结果必须由独立审阅复核，所以没有开始。'
            f'可在“执行后端”中改用 {_alternatives()}，或关闭事实核查后生成。')


def require_for_review(backend):
    if not restricted_review(backend):
        raise ReviewBackendUnsupported(
            f'{_label(backend)} 尚未验证受限独立审阅，不能运行独立审阅，也不会退回普通写权限。'
            f'可改用 {_alternatives()} 后重新审阅；已有稿件、编辑和普通下载不受影响。')


def delivery_blocker(backend):
    """None when the current backend could complete the missing review."""
    if restricted_review(backend):
        return None
    return {'code': CODE, 'message': f'当前执行后端 {_label(backend)} 尚未验证受限独立审阅；'
                                     f'正式交付需要先改用 {_alternatives()} 完成独立审阅。'}
