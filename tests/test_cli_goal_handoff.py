"""No-Scout CLI research still passes a real, inspectable packet to Analyst."""
import json
import sys
import pytest
from briefloop import research_plan
from briefloop.scout_coverage import declare
from briefloop.scout_tools import join_scouts
from briefloop.store import Store


def make_run(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Original', 'Revenue is 12 million. Profit is not disclosed.')
    run = store.create_run({'title': 'Quarter', 'objective': 'Explain revenue and profit',
        'key_questions': ['Revenue?', 'Profit?'], 'allow_web': False, 'fact_check': False,
        'research_strategy': 'goal_driven'}, [source['id']], research_protocol='quality_v1')
    plan = research_plan.freeze(store, run['id'])
    return store, run['id'], source, plan


def test_cli_empty_join_after_close_keeps_evidence_open_question_and_gap(tmp_path, monkeypatch, capsys):
    from briefloop.cli import main
    store, rid, source, plan = make_run(tmp_path)
    declare(store, rid, [])
    handoff = tmp_path/'research'/rid/'rounds'/'1'/'handoff.json'
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(json.dumps({'learnings': [], 'question_coverage': [
        {'question_id': 'q1', 'status': 'answered', 'reason': 'Revenue reported',
         'evidence': [{'source_id': source['id'], 'locator': 'line 1', 'excerpt': 'Revenue is 12 million.'}]},
        {'question_id': 'q2', 'status': 'open', 'reason': 'Not disclosed',
         'remaining_question': 'Profit?', 'evidence': []}]}))
    research_plan.finish_round(store, rid, summary='Revenue known; profit unknown',
                               gaps=[{'description': 'Profit not disclosed', 'source_ids': [source['id']]}])
    target = tmp_path/'joined-scouts.json'
    monkeypatch.setattr(sys, 'argv', ['briefloop', 'tool', '--workspace', str(tmp_path),
        'join-scouts', '--run', rid, '--output', str(target)])
    main()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == json.loads(target.read_text())
    assert receipt['sources'] == []  # No fabricated Scout result.
    coverage = next(n['question_coverage'] for n in receipt['retrieval_notes'] if n.get('kind') == 'research_round_closeout')
    assert coverage[0]['evidence'][0]['source_id'] == source['id']
    assert coverage[1]['status'] == 'open' and coverage[1]['remaining_question'] == 'Profit?'
    assert any(g['description'] == 'Profit not disclosed' for g in receipt['gap_records'])
    assert research_plan.require_writing_closeout(store, rid)


def test_empty_join_cannot_skip_undeclared_or_promised_scout(tmp_path):
    store, rid, source, plan = make_run(tmp_path)
    with pytest.raises(ValueError, match='scout_tasks'):
        join_scouts(store, [], run_id=rid)
    declare(store, rid, [{'slot_id': 'a', 'assignment': 'Read profit', 'result_file': str(tmp_path/'a.json')}])
    with pytest.raises(ValueError, match='scout_tasks'):
        join_scouts(store, [], run_id=rid)
    with pytest.raises(ValueError, match='--run'):
        join_scouts(store, [])
