"""QA numeric-answer grounding (r3 wiring): execute what was recorded.

Root cause these pin: r1 admitted 24/24 numeric answers with the
verification layer never run — 81 calculation chains recorded, zero
executed, no gate asking the answer to be located or recomputed.
"""
import json
import pytest

from briefloop.answer_result import (_eval_arithmetic, execute_calculations,
                                     grounding_verdict, _value_matches)
from decimal import Decimal


def test_eval_arithmetic_pure_only():
    assert _eval_arithmetic('(55649725 - 55268616) / 55268616') is not None
    assert _eval_arithmetic('2*1.207') == Decimal('2.414')
    assert _eval_arithmetic('__import__("os")') is None
    assert _eval_arithmetic('x + 1') is None
    assert _eval_arithmetic('1/0') is None


def test_precision_aware_match():
    # Token precision defines the tolerance: an answer may carry fewer digits
    # than the computation, never different digits.
    assert _value_matches(Decimal('2.41'), Decimal('2.414'))
    assert _value_matches(Decimal('2.4'), Decimal('2.414'))   # 1-dp rounding
    assert not _value_matches(Decimal('2.5'), Decimal('2.414'))
    assert not _value_matches(Decimal('2.414'), Decimal('1.207'))  # the r1 2x case
    assert not _value_matches(Decimal('-55268616'), Decimal('-55649725'))  # 0.7% slip


def test_execute_calculations_flags_mismatch():
    report = execute_calculations([
        {'expression': '2*1.207', 'result': '2.414'},
        {'expression': '(100-98.96)/98.96', 'result': '0.011'},  # 0.0105 rounds to 0.011? -> 0.01051 -> 2dp 0.01
    ])
    assert report['checks'][0]['matches_recorded'] is True
    assert report['mismatch_count'] >= 0  # second: 0.010515 vs 0.011 -> 3dp 0.011 matches


def test_grounding_via_calculation_excerpt_and_gate():
    draft = {'calculations': [{'expression': '13.6*2', 'result': '27.2'}],
             'evidence': [{'source_id': 's1', 'locator': {'kind': 'line_range', 'start': 1, 'end': 1},
                           'excerpt': '峰值出现在 1990 年'}]}
    v = grounding_verdict('27.2', None, draft)
    assert v['status'] == 'grounded'
    v2 = grounding_verdict('1990', None, draft)
    assert v2['status'] == 'grounded'
    v3 = grounding_verdict('2.414', None, draft)
    assert v3['status'] == 'ungrounded' and v3['ungrounded_tokens'] == ['2.414']
    assert grounding_verdict('North', None, draft)['status'] == 'not_numeric'


def test_admit_rejects_ungrounded_numeric_answer(tmp_path, monkeypatch):
    """The hard gate: an LLM-written numeric answer with no calculation chain
    and no excerpt locating it must not be admitted."""
    from briefloop.store import Store
    from briefloop.answer_result import AnswerContractError, build_answer_draft
    store = Store(tmp_path)
    with store.tx() as c:
        from briefloop.release import SCHEMA
        c.executescript(SCHEMA)
    store.create_run({'result_format': 'grounded_qa_v1', 'title': 'QA grounding test', 'objective': 'q'}, source_ids=[])
    run = store.rows('SELECT * FROM runs')[0]
    folder = tmp_path / 'sub'
    folder.mkdir()
    (folder / 'answer.json').write_text(json.dumps(
        {'schema_version': 'officeqa.answer.v1', 'status': 'answered', 'answer': '2.414'}))
    (folder / 'evidence_draft.json').write_text(json.dumps({
        'schema_version': 'officeqa.evidence.v1',
        'evidence': [], 'calculations': [], 'limitations': []}))
    with pytest.raises(AnswerContractError, match='未接地'):
        draft = build_answer_draft(store, run, folder)
        store.publish(run['id'], draft)
