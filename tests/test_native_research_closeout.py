"""Writing admission uses real saved rounds; the paid Analyst boundary is a spy."""
import json
import threading
from types import SimpleNamespace

import pytest

from briefloop import native_orchestrator, research_plan
from briefloop.native_roles import ToolError
from briefloop.research_budget import snapshot
from briefloop.research_handoff import gap_view
from briefloop.store import Store, dump


def prepared(tmp_path, monkeypatch, *, protocol='quality_v1', deep=False):
    store = Store(tmp_path)
    source = store.add_source('Synthetic secondary release', 'Public beta is available; original terms were not retrieved.')
    run = store.create_run({'title': 'Synthetic weekly', 'objective': 'Explain available terms.', 'allow_web': False,
        'research_tier': 'deep' if deep else 'standard',
        'research_budget': {'search_requests': 0, 'candidate_urls': 0, 'source_pages': 0}},
        [source['id']], research_protocol=protocol)
    job = store.enqueue('generate', {'run_id': run['id']})
    store.update_job(job['id'], status='running')
    packet = store.root / 'native' / 'packet'
    packet.mkdir(parents=True)
    native_orchestrator._save(packet.parent / 'plan.json', {'summary': 'Explain available terms'})
    native_orchestrator._save(packet.parent / 'research.json', {'sources': [], 'gaps': ['Original terms not retrieved']})
    harness = SimpleNamespace(cancel_requested=lambda _: False, _lock=threading.RLock())
    config = {'run_id': run['id'], 'job_id': job['id'], 'session_id': 'offline-spy',
              'packet_root': str(packet), '_harness': harness}
    calls = []
    def writer_boundary(store, config, directory, callback):
        calls.append(config['run_id'])
        directory.mkdir(parents=True, exist_ok=True)
        native_orchestrator._save(directory / 'draft.json', {'title': 'Boundary fixture', 'markdown': 'Fixture only.'})
        return {'boundary_reached': True}
    monkeypatch.setattr(native_orchestrator, '_child', writer_boundary)
    return store, run, config, calls


def test_active_round_rejects_before_writer_then_closed_gaps_and_exhausted_budget_are_allowed(tmp_path, monkeypatch):
    store, run, config, calls = prepared(tmp_path, monkeypatch, deep=True)
    rid = run['id']
    plan = research_plan.freeze(store, rid, preset='deep', structure={'breadth': 1, 'depth': 3, 'parallel': 1})
    before_plan = dump(plan)
    before_budget = snapshot(store, rid)
    before_files = {name: (tmp_path / 'native' / name).read_bytes() for name in ('plan.json', 'research.json')}
    with pytest.raises(ToolError, match='finish_research_round'):
        native_orchestrator.write_report(store, config, {})
    assert not calls and not (tmp_path / 'native/analyst').exists()
    assert dump(research_plan.frozen(store, rid)) == before_plan
    assert snapshot(store, rid) == before_budget
    assert all((tmp_path / 'native' / name).read_bytes() == data for name, data in before_files.items())
    native_orchestrator.action(store, config, {'request': {'action': 'finish_research_round',
        'summary': 'Keep beta scope; original terms remain unavailable within the authorized budget.',
        'gaps': [{'description': 'Original terms not retrieved'}]}})
    closed = research_plan.frozen(store, rid)
    assert len(closed['rounds']) == 3
    assert sum(r['status'] == 'pending' for r in closed['rounds'].values()) == 2
    assert gap_view(store, rid)['gaps'] == ['Original terms not retrieved']
    budget = snapshot(store, rid)
    native_orchestrator.write_report(store, config, {})
    assert calls == [rid]
    assert research_plan.frozen(store, rid) == closed and snapshot(store, rid) == budget
    assert gap_view(store, rid)['gaps'] == ['Original terms not retrieved']


def test_latest_opened_round_cannot_borrow_an_older_closeout(tmp_path, monkeypatch):
    store, run, config, calls = prepared(tmp_path, monkeypatch)
    rid = run['id']
    research_plan.freeze(store, rid)
    research_plan.finish_round(store, rid, summary='Older round')
    original = research_plan.frozen(store, rid)
    # Simulate an interrupted latest round, including a missing current-round pointer.
    for status in ('active', 'failed'):
        plan = json.loads(dump(original))
        plan['current_round_id'] = None
        plan['rounds']['interrupted_round'] = {'index': 2, 'created': 'synthetic', 'status': status, 'outcome': None}
        store.set_meta('research_plan:' + rid, plan)
        with pytest.raises(ToolError, match='收轮'):
            native_orchestrator.write_report(store, config, {})
        assert research_plan.frozen(store, rid) == plan
    assert not calls


def test_quality_missing_plan_or_outcome_rejected_but_old_closed_outcome_remains_compatible(tmp_path, monkeypatch):
    store, run, config, calls = prepared(tmp_path, monkeypatch)
    rid = run['id']
    with pytest.raises(ToolError, match='freeze_research_plan'):
        native_orchestrator.write_report(store, config, {})
    research_plan.freeze(store, rid)
    research_plan.finish_round(store, rid, summary='')
    plan = research_plan.frozen(store, rid)
    last = next(iter(plan['rounds'].values()))
    last['outcome'] = None
    store.set_meta('research_plan:' + rid, plan)
    with pytest.raises(ToolError, match='收轮记录'):
        native_orchestrator.write_report(store, config, {})
    last['outcome'] = {'summary': '', 'gap_ids': []}  # Existing v1 records predate closeout.
    store.set_meta('research_plan:' + rid, plan)
    native_orchestrator.write_report(store, config, {})
    assert calls == [rid] and research_plan.frozen(store, rid) == plan


def test_legacy_keeps_existing_writer_path_without_a_frozen_research_plan(tmp_path, monkeypatch):
    store, run, config, calls = prepared(tmp_path, monkeypatch, protocol=None)
    native_orchestrator.write_report(store, config, {})
    assert calls == [run['id']]
    assert research_plan.frozen(store, run['id']) is None
