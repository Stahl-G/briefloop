"""grounded_qa_v1 output mode: answer contract, mechanical projection, evidence check.

Protocol BL-OQA-SR-v1.0 §3.1/§3.2/§5.3, design-0223 §3.Q1. The saved answer
record is the only scored artifact; every projection here is mechanical and
gold-blind, and the shortest review projection is derived by the program —
no LLM rewrites the brief. The gold-blind rules mirror the experiment-side
adapter ``experiments/officeqa_structured/score_answer_record.py`` (Q0); a
regression test pins both implementations to the same verdicts, because the
product package cannot import the experiment tree at runtime.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RESULT_FORMAT = 'grounded_qa_v1'
ANSWER_SCHEMA_VERSION = 'officeqa.answer.v1'
EVIDENCE_SCHEMA_VERSION = 'officeqa.evidence.v1'
KNOWN_ANSWER_FIELDS = frozenset({'schema_version', 'status', 'answer'})
KNOWN_EVIDENCE_FIELDS = frozenset({'schema_version', 'evidence', 'calculations', 'limitations'})
MAX_ANSWER_CHARS = 250

_FINAL_ANSWER_LABEL_RE = re.compile(r'final_answer', re.IGNORECASE)


class AnswerContractError(ValueError):
    """A QA-mode submission does not satisfy the answer/attachment contract."""


@dataclass(frozen=True)
class AnswerProjection:
    """Gold-blind result of validating one answer.json payload."""

    status: str  # "answered" | "abstained" | "invalid"
    answer: str | None = None  # stripped answer text, "answered" only
    reason: str = ""  # why abstained/invalid; "" for answered
    warnings: tuple[str, ...] = field(default=())

    @property
    def submission_text(self) -> str:
        if self.status != 'answered' or self.answer is None:
            raise ValueError(f"no submission text for status={self.status!r}")
        return f"<FINAL_ANSWER>{self.answer}</FINAL_ANSWER>"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate JSON key: {key}")
        seen.add(key)
    return dict(pairs)


def _decode(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError(f"answer.json is not valid UTF-8: {exc}") from exc
    return raw


def project_answer(raw_answer: str | bytes) -> AnswerProjection:
    """Validate one answer.json payload without any access to gold.

    Same rules as the Q0 adapter: single strict JSON object (duplicate keys,
    trailing objects, Markdown fences, NaN/Infinity and illegal UTF-8 are all
    rejected), ``answer`` is always a string, single line, <=250 chars after
    stripping, and never embeds a FINAL_ANSWER label.
    """
    try:
        text = _decode(raw_answer)
        record = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        # Covers non-JSON payloads, Markdown fences, NaN/Infinity and
        # trailing extra objects ("Extra data") alike.
        return AnswerProjection('invalid', reason=f'answer payload is not a single strict JSON object: {exc}')

    if not isinstance(record, dict):
        return AnswerProjection('invalid', reason=f'answer payload must be one JSON object, got {type(record).__name__}')

    warnings: list[str] = []
    extra = sorted(set(record) - KNOWN_ANSWER_FIELDS)
    if extra:
        warnings.append(f'extra fields ignored, never passed to the scorer: {extra}')

    schema_version = record.get('schema_version')
    if schema_version != ANSWER_SCHEMA_VERSION:
        return AnswerProjection(
            'invalid',
            reason=f'schema_version must be {ANSWER_SCHEMA_VERSION!r}, got {schema_version!r}',
            warnings=tuple(warnings),
        )

    status = record.get('status')
    answer = record.get('answer', ...)
    if answer is ...:
        return AnswerProjection('invalid', reason='missing required field: answer', warnings=tuple(warnings))

    if status == 'abstained':
        if answer is not None:
            return AnswerProjection(
                'invalid',
                reason='status=abstained must pair with answer=null',
                warnings=tuple(warnings),
            )
        return AnswerProjection('abstained', reason='abstained', warnings=tuple(warnings))

    if status != 'answered':
        return AnswerProjection(
            'invalid',
            reason=f"status must be 'answered' or 'abstained', got {status!r}",
            warnings=tuple(warnings),
        )

    if not isinstance(answer, str):
        return AnswerProjection(
            'invalid',
            reason=f'answer must be a string for status=answered, got {type(answer).__name__}',
            warnings=tuple(warnings),
        )

    stripped = answer.strip()
    if not stripped:
        return AnswerProjection('invalid', reason='answer is empty after stripping whitespace', warnings=tuple(warnings))
    if len(stripped.splitlines()) > 1:
        return AnswerProjection('invalid', reason='answer must be a single line', warnings=tuple(warnings))
    if len(stripped) > MAX_ANSWER_CHARS:
        return AnswerProjection(
            'invalid',
            reason=f'answer exceeds {MAX_ANSWER_CHARS} characters after stripping: {len(stripped)}',
            warnings=tuple(warnings),
        )
    if _FINAL_ANSWER_LABEL_RE.search(stripped):
        return AnswerProjection(
            'invalid',
            reason='answer embeds a FINAL_ANSWER label (case-insensitive); exactly one tag pair is added by the projection only',
            warnings=tuple(warnings),
        )

    return AnswerProjection('answered', answer=stripped, warnings=tuple(warnings))


def canonical_answer(projection: AnswerProjection) -> dict[str, Any]:
    """The three contract fields as saved on a version (extras never ride along)."""
    return {'schema_version': ANSWER_SCHEMA_VERSION, 'status': projection.status, 'answer': projection.answer}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _canonical_field(value: Any, what: str) -> str:
    try:
        return _canonical(value)
    except (TypeError, ValueError) as exc:
        raise AnswerContractError(what + ' 不是可序列化为 JSON 的对象：' + str(exc)) from None


def answer_sha256(answer: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(answer).encode('utf-8')).hexdigest()


def answer_identity(detail: dict[str, Any]) -> dict[str, Any]:
    """Answer + attachment identity for fingerprints and readers (protocol §3.3)."""
    answer = detail.get('answer_result')
    evidence = detail.get('answer_evidence')
    projection = project_answer(_canonical(answer)) if isinstance(answer, dict) else AnswerProjection('invalid', reason='missing answer_result')
    return {
        'schema_version': ANSWER_SCHEMA_VERSION,
        'status': projection.status,
        'answer': answer if isinstance(answer, dict) else None,
        'answer_sha256': answer_sha256(answer) if isinstance(answer, dict) else None,
        'evidence': evidence if isinstance(evidence, dict) else None,
        'evidence_sha256': hashlib.sha256(_canonical(evidence).encode('utf-8')).hexdigest() if isinstance(evidence, dict) else None,
    }


# --- optional evidence attachment (read-only validation) ---------------------

def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_bare_url(value: Any) -> bool:
    return isinstance(value, str) and bool(re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', value.strip()) or value.strip().startswith('www.'))


def _err(errors: list[dict[str, str]], path: str, code: str, message: str) -> None:
    errors.append({'path': path, 'code': code, 'message': message})


def _join(errors: list[dict[str, str]]) -> str:
    return '；'.join(f"{e['path']}：{e['message']}" for e in errors)


def _locator_error(value: Any) -> str | None:
    if not isinstance(value, dict):
        return 'locator 必须是对象'
    kind = value.get('kind')
    if kind == 'line_range':
        start, end = value.get('start'), value.get('end')
        if set(value) - {'kind', 'start', 'end'}:
            return 'line_range 定位含未知字段：' + ','.join(sorted(set(value) - {'kind', 'start', 'end'}))
        if not _is_int(start) or not _is_int(end) or not 1 <= start <= end:
            return f'line_range 定位不可解析：start={start!r}, end={end!r}（需要 1-based 整数且 start≤end）'
        return None
    if kind == 'page':
        if set(value) - {'kind', 'page_index', 'element_index'}:
            return 'page 定位含未知字段：' + ','.join(sorted(set(value) - {'kind', 'page_index', 'element_index'}))
        page, element = value.get('page_index'), value.get('element_index')
        if not _is_int(page) or page < 0:
            return f'page 定位不可解析：page_index={page!r}（需要 0-based 整数）'
        if element is not None and (not _is_int(element) or element < 0):
            return f'page 定位不可解析：element_index={element!r}（需要 0-based 整数）'
        return None
    return f'未知 locator.kind：{kind!r}（仅支持 line_range 或 page）'


def locator_text(locator: dict[str, Any]) -> str:
    """One-line human-readable locator for citations and the projection."""
    if locator.get('kind') == 'line_range':
        start, end = locator.get('start'), locator.get('end')
        return f'line {start}' if start == end else f'line {start}-{end}'
    text = f"page {locator.get('page_index')}"
    if locator.get('element_index') is not None:
        text += f" element {locator.get('element_index')}"
    return text


def validate_evidence(store, run_id, raw: str | bytes | None) -> dict[str, Any]:
    """Read-only attachment check; never writes and never touches gold.

    Hard gates (protocol/design Q1): every ``source_id`` must be a source
    registered on this run — a bare URL or invented id is rejected with a
    ``bare_url``/``source_not_in_run`` code — and every locator must be
    parseable. Whether the excerpt actually locates in the saved source text
    is recorded per entry as a diagnostic, not a gate.
    """
    report: dict[str, Any] = {'status': 'ok', 'errors': [], 'warnings': [], 'entries': [],
                               'calculations': 0, 'limitations': 0}
    errors: list[dict[str, str]] = report['errors']
    if raw is None:
        return report
    try:
        value = json.loads(_decode(raw), parse_constant=_reject_constant, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, json.JSONDecodeError) as exc:
        _err(errors, '$', 'payload_invalid', '附件不是单个严格 JSON 对象：' + str(exc))
        report['status'] = 'invalid'
        return report
    if not isinstance(value, dict):
        _err(errors, '$', 'payload_invalid', '附件必须是单个 JSON 对象')
        report['status'] = 'invalid'
        return report
    if value.get('schema_version') != EVIDENCE_SCHEMA_VERSION:
        _err(errors, 'schema_version', 'schema_version',
             f'schema_version 必须是 {EVIDENCE_SCHEMA_VERSION!r}，got {value.get("schema_version")!r}')
    extra = sorted(set(value) - KNOWN_EVIDENCE_FIELDS)
    if extra:
        report['warnings'].append('顶层额外字段忽略，不会进入评分：' + ','.join(extra))
    allowed = set(store.source_ids(run_id)) if store is not None and run_id else None

    entries = value.get('evidence')
    if not isinstance(entries, list):
        _err(errors, 'evidence', 'evidence_not_array', 'evidence 必须是数组')
        entries = []
    for index, item in enumerate(entries):
        prefix = f'evidence[{index}]'
        if not isinstance(item, dict) or set(item) - {'source_id', 'locator', 'excerpt'}:
            _err(errors, prefix, 'item_unknown_field', '条目只允许 source_id/locator/excerpt')
            continue
        sid = item.get('source_id')
        locator = item.get('locator')
        excerpt = item.get('excerpt', '')
        if not isinstance(sid, str) or not sid:
            _err(errors, prefix + '.source_id', 'source_id_missing', '缺少 source_id')
            continue
        if _is_bare_url(sid):
            _err(errors, prefix + '.source_id', 'bare_url',
                 'source_id 不能是裸 URL；请先用资料工具登记来源并使用返回的真实 source_id')
            continue
        if allowed is not None and sid not in allowed:
            _err(errors, prefix + '.source_id', 'source_not_in_run',
                 f'{sid!r} 不属于本任务已登记来源；请先用资料工具登记并使用返回的真实 source_id')
            continue
        error = _locator_error(locator)
        if error:
            _err(errors, prefix + '.locator', 'locator_invalid', error)
            continue
        if not isinstance(excerpt, str):
            _err(errors, prefix + '.excerpt', 'excerpt_type', 'excerpt 必须是字符串')
            continue
        located = None
        if store is not None and run_id and locator['kind'] == 'line_range':
            try:  # Diagnostic only: an unlocatable excerpt never flips the answer.
                lines = store.source_text(sid).splitlines()
                located = excerpt in '\n'.join(lines[locator['start'] - 1:locator['end']]) if excerpt else None
            except (ValueError, OSError):
                located = None
        report['entries'].append({'index': index, 'source_id': sid, 'locator': locator,
                                  'excerpt_located': located})
    report['calculations'] = _check_calculations(value.get('calculations'), len(entries), errors)
    limitations = value.get('limitations')
    if not isinstance(limitations, list) or any(not isinstance(x, str) for x in limitations):
        _err(errors, 'limitations', 'limitations_invalid', 'limitations 必须是字符串数组')
    else:
        report['limitations'] = len(limitations)
    if errors:
        report['status'] = 'invalid'
    return report


def _check_calculations(value, evidence_count: int, errors: list[dict[str, str]]) -> int:
    if value is None:
        return 0
    if not isinstance(value, list):
        _err(errors, 'calculations', 'calculations_invalid', 'calculations 必须是数组')
        return 0
    for index, item in enumerate(value):
        prefix = f'calculations[{index}]'
        if not isinstance(item, dict) or set(item) - {'expression', 'inputs', 'result'}:
            _err(errors, prefix, 'item_unknown_field', '条目只允许 expression/inputs/result')
            continue
        if not isinstance(item.get('expression'), str) or not isinstance(item.get('result'), str):
            _err(errors, prefix, 'calculation_type', 'expression 与 result 必须是字符串')
            continue
        inputs = item.get('inputs')
        if not isinstance(inputs, list):
            _err(errors, prefix + '.inputs', 'inputs_invalid', 'inputs 必须是数组')
            continue
        for number, entry in enumerate(inputs):
            where = f'{prefix}.inputs[{number}]'
            if not isinstance(entry, dict) or set(entry) - {'name', 'value', 'evidence_index'}:
                _err(errors, where, 'item_unknown_field', '条目只允许 name/value/evidence_index')
                continue
            if not isinstance(entry.get('name'), str) or not isinstance(entry.get('value'), str):
                _err(errors, where, 'input_type', 'name 与 value 必须是字符串')
            position = entry.get('evidence_index')
            if position is not None and (not _is_int(position) or not 0 <= position < evidence_count):
                _err(errors, where + '.evidence_index', 'evidence_index_range', '超出 evidence 数组范围')
    return len(value)


# --- deterministic calculation execution & answer grounding (r3 wiring) ----
#
# OfficeQA r1 post-mortem: 24/24 numeric answers were admitted with the
# verification layer never executed; the attachment carried 81 recorded
# calculation chains that nothing ran.  These helpers execute recorded
# arithmetic deterministically and require every numeric answer token to be
# grounded — either recomputed by a recorded calculation or located verbatim
# in an evidence excerpt.  Lookup answers ground via excerpts; computed
# answers ground via calculations.  Both checks are mechanical.

import ast
from decimal import Decimal, InvalidOperation

_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub,
                  ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Constant)
_ALLOWED_CONST_TYPES = (int, float)


def _eval_arithmetic(expression: str) -> Decimal | None:
    """Evaluate a pure-arithmetic expression; anything else is refused."""
    try:
        tree = ast.parse(expression, mode='eval')
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return None
        if isinstance(node, ast.Constant) and not isinstance(node.value, _ALLOWED_CONST_TYPES):
            return None
    try:
        value = eval(compile(tree, '<calculation>', 'eval'), {'__builtins__': {}}, {})
    except (ZeroDivisionError, ArithmeticError, TypeError):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


_NUMBER_IN_TEXT = re.compile(r'(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_])')


def _numbers_in_text(text: str) -> list[Decimal]:
    values = []
    for token in _NUMBER_IN_TEXT.findall(text or ''):
        try:
            values.append(Decimal(token.replace(',', '')))
        except InvalidOperation:
            continue
    return values


def _value_matches(token: Decimal, computed: Decimal) -> bool:
    """Precision-aware match: the token rounds to the computed value.

    An answer ``2.41`` matches a computed ``2.414`` (2-decimals rounding);
    ``2.4`` does not.  Keeps rounding discipline honest without demanding
    more digits than the answer states.
    """
    if token == computed:
        return True
    decimals = max(0, -token.as_tuple().exponent)
    quantum = Decimal(1).scaleb(-decimals)
    return abs(computed - token) < Decimal('0.5').scaleb(-decimals) and \
        computed.quantize(quantum) == token.quantize(quantum)


def execute_calculations(calculations: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Deterministically re-run recorded calculation chains.

    Returns computed values, per-item match against the recorded result and
    diagnostics; never raises — an unexecutable expression is reported, not
    trusted.
    """
    checks, values = [], []
    for index, item in enumerate(calculations or []):
        if not isinstance(item, dict):
            continue
        computed = _eval_arithmetic(str(item.get('expression') or ''))
        recorded_tokens = _numbers_in_text(str(item.get('result') or ''))
        recorded = recorded_tokens[-1] if recorded_tokens else None
        matches = (computed is not None and recorded is not None
                   and _value_matches(recorded, computed))
        checks.append({'index': index, 'computed': str(computed) if computed is not None else None,
                       'recorded': str(recorded) if recorded is not None else None,
                       'executable': computed is not None, 'matches_recorded': matches})
        if computed is not None:
            values.append(computed)
    return {'checks': checks, 'values': values,
            'mismatch_count': sum(1 for c in checks if c['executable'] and not c['matches_recorded'])}


def grounding_verdict(answer: str | None, evidence_report: dict[str, Any],
                      evidence_draft: dict[str, Any] | None) -> dict[str, Any]:
    """Every numeric token in an answered value must be mechanically grounded:
    recomputed by a recorded calculation, or present verbatim in an evidence
    excerpt.  Text answers and abstentions are out of scope by construction.
    """
    tokens = _numbers_in_text(str(answer or ''))
    if not tokens:
        return {'status': 'not_numeric'}
    execution = execute_calculations((evidence_draft or {}).get('calculations'))
    excerpts = [str(e.get('excerpt') or '') for e in (evidence_draft or {}).get('evidence') or []
                if isinstance(e, dict)]
    excerpt_numbers = [n for text in excerpts for n in _numbers_in_text(text)]
    ungrounded = [str(t) for t in tokens
                  if not any(_value_matches(t, v) for v in execution['values'])
                  and not any(_value_matches(t, n) for n in excerpt_numbers)]
    verdict = {'status': 'grounded' if not ungrounded else 'ungrounded',
               'answer_numeric_tokens': [str(t) for t in tokens],
               'ungrounded_tokens': ungrounded,
               'calculation_count': len(execution['values']),
               'calculation_mismatch_count': execution['mismatch_count'],
               'calculation_checks': execution['checks']}
    if evidence_report is not None:
        evidence_report.setdefault('grounding', verdict)
    return verdict


def canonical_evidence(raw: str | bytes) -> dict[str, Any]:
    """The attachment as saved on a version: contract fields only."""
    value = json.loads(_decode(raw), parse_constant=_reject_constant, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(value, dict):
        raise AnswerContractError('evidence_draft.json 必须是单个 JSON 对象')
    return {key: value.get(key) for key in ('schema_version', 'evidence', 'calculations', 'limitations')}


# --- deterministic shortest projection ---------------------------------------

def _one_line(text: Any) -> str:
    return ' '.join(str(text or '').split())


def qa_projection(requirements: dict[str, Any], answer: dict[str, Any], evidence: dict[str, Any] | None) -> str:
    """The only body a QA version may carry: question, answer, evidence entries.

    Pure function of the saved requirements, the canonical answer record and
    the canonical attachment. Admission and review both recompute it, so an
    LLM-written (or hand-edited) brief can never replace the projection.
    """
    lines = ['# ' + _one_line(requirements.get('title') or '问答任务'), '']
    lines.append('问题：' + _one_line(requirements.get('objective') or ''))
    for question in requirements.get('key_questions') or []:
        if isinstance(question, str) and question.strip():
            lines.append('- ' + _one_line(question))
    lines.append('')
    if answer.get('status') == 'abstained':
        lines.append('答案：（abstained，未作答）')
    else:
        lines.append('答案：' + _one_line(answer.get('answer')))
    citations = projection_citations(evidence)
    if citations:
        lines += ['', '证据入口：']
        lines += [f"- [@{ref['source_id']}] {ref['locator']}" for ref in citations]
    limitations = [_one_line(x) for x in (evidence or {}).get('limitations') or [] if isinstance(x, str) and x.strip()][:5]
    if limitations:
        lines += ['', '记录的局限：' + '；'.join(x[:240] for x in limitations)]
    return '\n'.join(lines) + '\n'


def projection_citations(evidence: dict[str, Any] | None) -> list[dict[str, str]]:
    """Evidence entries as version citations, in attachment order, deduplicated."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in (evidence or {}).get('evidence') or []:
        if not isinstance(item, dict):
            continue
        sid = item.get('source_id')
        if not isinstance(sid, str) or not sid:
            continue
        locator = locator_text(item.get('locator') or {})
        if (sid, locator) in seen:
            continue
        seen.add((sid, locator))
        out.append({'source_id': sid, 'locator': locator, 'excerpt': ''})
    return out


def is_qa(requirements: dict[str, Any] | str) -> bool:
    if isinstance(requirements, str):
        requirements = json.loads(requirements)
    return requirements.get('result_format') == RESULT_FORMAT


def admit_answer(store, run, draft) -> None:
    """Persist-time gate shared by every QA version admission path.

    A QA run must carry a contract-valid answer, and its body must equal the
    mechanical projection of that answer — an LLM-written brief can never pose
    as the version content. Non-QA runs must not carry answer fields at all.
    """
    requirements = json.loads(run['requirements'])
    qa = is_qa(requirements)
    if draft.answer_result is None:
        if qa:
            raise AnswerContractError('grounded_qa_v1 任务必须随版本保存 answer.json 答案记录，不能只交证据附件')
        if draft.answer_evidence is not None:
            raise AnswerContractError('answer_result/answer_evidence 仅属于 grounded_qa_v1 任务，普通报告不能保存答案字段')
        return
    if not qa:
        raise AnswerContractError('answer_result/answer_evidence 仅属于 grounded_qa_v1 任务，普通报告不能保存答案字段')
    projection = project_answer(_canonical_field(draft.answer_result, 'answer_result'))
    if projection.status == 'invalid':
        raise AnswerContractError('保存的答案记录不符合契约：' + projection.reason)
    if draft.answer_evidence is not None:
        report = validate_evidence(store, run['id'], _canonical_field(draft.answer_evidence, 'answer_evidence'))
        if report['errors']:
            raise AnswerContractError('保存的证据附件未通过校验：' + _join(report['errors']))
    if projection.status == 'answered':
        draft_evidence = None
        if draft.answer_evidence is not None:
            raw_ev = _canonical_field(draft.answer_evidence, 'answer_evidence')
            if isinstance(raw_ev, dict):
                draft_evidence = raw_ev
            elif isinstance(raw_ev, (str, bytes)):
                try:
                    draft_evidence = json.loads(_decode(raw_ev))
                except (ValueError, json.JSONDecodeError):
                    draft_evidence = None
        verdict = grounding_verdict(projection.answer, None, draft_evidence)
        if verdict['status'] == 'ungrounded':
            raise AnswerContractError(
                '数值答案未接地：' + ', '.join(verdict['ungrounded_tokens'])
                + '。数值答案必须在 evidence_draft 中给出可重算的 calculations（程序会确定性执行并按答案精度比对结果），'
                  '或在 evidence 摘录中逐字定位到该数值；二者都不满足的数值答案不予接纳。')
    expected = qa_projection(requirements, draft.answer_result, draft.answer_evidence)
    if draft.markdown != expected:
        raise AnswerContractError('QA 版本正文必须与答案的机械投影一致（问题、答案、证据入口），不能另行撰写或改写')
    if [(ref.source_id, ref.locator) for ref in draft.citations] != [
            (ref['source_id'], ref['locator']) for ref in projection_citations(draft.answer_evidence)]:
        raise AnswerContractError('QA 版本引用必须与证据入口一一对应')


def build_answer_draft(store, run, folder) -> dict[str, Any]:
    """Mechanically build the version payload from a submitted answer.json.

    Reads ``answer.json`` (required) and ``evidence_draft.json`` (optional)
    from ``folder``; the returned dict is a BriefDraft payload whose markdown
    is the deterministic projection. Raises AnswerContractError with the
    concrete reason when the submission violates the contract.
    """
    folder = Path(folder)
    answer_path = folder / 'answer.json'
    if not answer_path.exists():
        raise AnswerContractError('缺少 answer.json（grounded_qa_v1 任务必须提交答案文件）')
    projection = project_answer(answer_path.read_bytes())
    if projection.status == 'invalid':
        raise AnswerContractError('answer.json：' + projection.reason)
    answer = canonical_answer(projection)
    evidence = None
    evidence_path = folder / 'evidence_draft.json'
    if evidence_path.exists():
        report = validate_evidence(store, run['id'], evidence_path.read_bytes())
        if report['errors']:
            raise AnswerContractError('evidence_draft.json：' + _join(report['errors']))
        evidence = canonical_evidence(evidence_path.read_bytes())
    requirements = json.loads(run['requirements'])
    return {
        'title': (_one_line(requirements.get('title')) or '问答任务')[:200],
        'markdown': qa_projection(requirements, answer, evidence),
        'citations': projection_citations(evidence),
        'answer_result': answer,
        'answer_evidence': evidence,
    }


def answer_of(store, version_id: str) -> dict[str, Any] | None:
    """Read a saved version's accepted answer; None for non-QA versions.

    The reader used after a run exits or fails: once a version is admitted,
    its answer stays readable regardless of later scoring or review failures.
    """
    brief = store.one('briefs', version_id)
    detail = json.loads(brief['detail'])
    if not isinstance(detail.get('answer_result'), dict):
        return None
    identity = answer_identity(detail)
    identity['version_id'] = version_id
    identity['brief_hash'] = brief['hash']
    identity['created'] = brief['created']
    return identity


def check_files(store, answer_path, evidence_path=None, run_id=None) -> dict[str, Any]:
    """Self-check command behind ``briefloop tool check-answer`` (no admission)."""
    report: dict[str, Any] = {'status': 'ok', 'errors': [], 'warnings': []}
    projection = project_answer(Path(answer_path).expanduser().read_bytes())
    report['answer'] = {'status': projection.status, 'reason': projection.reason,
                        'answer': projection.answer, 'warnings': list(projection.warnings)}
    report['warnings'].extend(projection.warnings)
    if projection.status == 'invalid':
        report['errors'].append(projection.reason)
    draft_evidence = None
    if evidence_path:
        path = Path(evidence_path).expanduser()
        if path.exists():
            evidence = validate_evidence(store if run_id else None, run_id, path.read_bytes())
            report['evidence'] = evidence
            report['warnings'].extend(evidence['warnings'])
            report['errors'].extend(evidence['errors'])
            try:
                draft_evidence = json.loads(_decode(path.read_bytes()))
            except (ValueError, json.JSONDecodeError):
                draft_evidence = None
        else:
            report['warnings'].append('evidence 文件不存在，按无附件检查：' + str(evidence_path))
    if projection.status == 'answered':
        verdict = grounding_verdict(projection.answer, None, draft_evidence)
        report['grounding'] = verdict
        if verdict['status'] == 'ungrounded':
            report['errors'].append(
                '数值答案未接地：' + ', '.join(verdict['ungrounded_tokens'])
                + '；需 calculations 重算一致或 evidence 摘录逐字含该数值')
    if report['errors']:
        report['status'] = 'invalid'
    return report


# --- agent-facing schemas and content method (protocol §5.3) ----------------
# Same constraints as experiments/officeqa_structured/schemas/*.json; a test
# pins the two copies together so they cannot drift.

ANSWER_SCHEMA_JSON = json.dumps({
    '$schema': 'http://json-schema.org/draft-07/schema#',
    '$id': 'officeqa.answer.v1',
    'title': 'OfficeQA structured short answer (answer.json)',
    'type': 'object',
    'required': ['schema_version', 'status', 'answer'],
    'additionalProperties': True,
    'properties': {
        'schema_version': {'const': 'officeqa.answer.v1'},
        'status': {'enum': ['answered', 'abstained']},
        'answer': {
            'description': 'answered 时为单行字符串（剥离首尾空白后 1..250 字符，不得嵌入 FINAL_ANSWER 标签，'
                           '列表按原题顺序如 "[North, 0.866]"，数值保持题目直接形式而不用 JSON number）；'
                           'abstained 时必须为 null（计 0 分）。',
            'anyOf': [
                {'type': 'null'},
                {'type': 'string', 'minLength': 1, 'maxLength': 250,
                 'pattern': '^[^\\n\\r]*$', 'not': {'pattern': 'FINAL_ANSWER'}},
            ],
        },
    },
}, ensure_ascii=False, indent=2)

EVIDENCE_SCHEMA_JSON = json.dumps({
    '$schema': 'http://json-schema.org/draft-07/schema#',
    '$id': 'officeqa.evidence.v1',
    'title': 'OfficeQA optional evidence attachment (evidence_draft.json)',
    'description': '可选证据与计算附件（协议 BL-OQA-SR-v1.0 §3.2）。记录公开可核对的原文定位、操作数与计算关系，'
                   '不要求保存模型隐藏思维链。source_id 只能使用本任务资料工具返回的真实 ID（不能填裸 URL）；'
                   '文本行号 1-based，PDF/JSON 的 page_index 0-based（不等于纸面页码）；结构化元素同时记录 '
                   'page_index 与 element_index。计算表达式是文本，不被 eval 执行。',
    'type': 'object',
    'required': ['schema_version', 'evidence', 'calculations', 'limitations'],
    'additionalProperties': True,
    'properties': {
        'schema_version': {'const': 'officeqa.evidence.v1'},
        'evidence': {
            'type': 'array',
            'items': {
                'type': 'object',
                'required': ['source_id', 'locator', 'excerpt'],
                'additionalProperties': False,
                'properties': {
                    'source_id': {'type': 'string', 'minLength': 1},
                    'locator': {
                        'oneOf': [
                            {'type': 'object', 'required': ['kind', 'start', 'end'], 'additionalProperties': False,
                             'properties': {'kind': {'const': 'line_range'},
                                            'start': {'type': 'integer', 'minimum': 1},
                                            'end': {'type': 'integer', 'minimum': 1}}},
                            {'type': 'object', 'required': ['kind', 'page_index'], 'additionalProperties': False,
                             'properties': {'kind': {'const': 'page'},
                                            'page_index': {'type': 'integer', 'minimum': 0},
                                            'element_index': {'type': 'integer', 'minimum': 0}}},
                        ],
                    },
                    'excerpt': {'type': 'string'},
                },
            },
        },
        'calculations': {
            'type': 'array',
            'items': {
                'type': 'object',
                'required': ['expression', 'inputs', 'result'],
                'additionalProperties': False,
                'properties': {
                    'expression': {'type': 'string'},
                    'inputs': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'required': ['name', 'value'],
                            'additionalProperties': False,
                            'properties': {
                                'name': {'type': 'string'},
                                'value': {'type': 'string'},
                                'evidence_index': {'type': 'integer', 'minimum': 0,
                                                   'description': '指向 evidence 数组下标。'},
                            },
                        },
                    },
                    'result': {'type': 'string'},
                },
            },
        },
        'limitations': {'type': 'array', 'items': {'type': 'string'}},
    },
}, ensure_ascii=False, indent=2)

QA_CONTENT_METHOD = '''本轮内容方法（BriefLoop QA profile，协议 §5.3）：回答该问题、识别证据不足或冲突、核对单位和计算。
- 只回答原题：答案保持题目要求的直接形式（数字、日期、文本或按原题顺序的列表字符串），不带“答案是”、解释、引用序号、置信度或替代答案，不使用 Markdown。
- 证据不足或材料冲突时如实记录：能在局限内作答就作答，并把局限写进证据附件 limitations；确实不能确定才弃权（status="abstained"、answer=null），不用弃权隐藏最佳可用答案。
- 单位与计算：保留原始单位、主体、期间与口径，换算前后核对量级；计算操作数取自已登记来源，计算关系写入附件 calculations（表达式是文本）。
- 不写报告正文；没有篇幅、文采、摘要或完整章节要求，不为格式消耗预算。'''

QA_SCOUT_CONTRACT = '''研究交接（grounded 问答）：按问题寻找并核对证据，返回事实、数值与单位、来源定位（source_id、1-based 行号或 0-based page_index）、覆盖状态、冲突与缺口。保留主体、期间、口径与实际/计划状态；不评价写作，不产出正文。'''
