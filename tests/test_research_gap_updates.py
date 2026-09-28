"""Explicit research gap updates survive round joins and both writer paths."""
import json
from types import SimpleNamespace

import pytest

from briefloop import analyst, native_orchestrator, research_plan
from briefloop.chat_tools import workspace_action
from briefloop.research_handoff import current_research, gap_view, read_state
from briefloop.scout_tools import HandoffError, check_handoff, join_scouts
from briefloop.store import Store


def setup(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = store.add_source('Original release', 'Public beta on September 10.\nPricing is 2 USD.\nRelease date is not stated.')
    run = store.create_run({'title': 'Weekly', 'objective': 'Report this week', 'allow_web': True},
                           [source['id']], research_protocol='quality_v1')
    research_plan.freeze(store, run['id'])
    path = store.root / 'scout.json'
    path.write_text(json.dumps({'sources': [], 'gaps': ['Missing original announcement', 'Missing pricing and release date']}))
    original = path.read_bytes()
    joined = join_scouts(store, [path], run_id=run['id'])
    return store, run['id'], source, path, original, joined


def update(joined, source, status='resolved', index=0):
    return {'gap_id': joined['gap_records'][index]['gap_id'], 'status': status,
            'reason': 'Original announcement has now been retrieved.',
            'evidence': [{'source_id': source['id'], 'locator': 'line 1', 'excerpt': 'Public beta on September 10.'}]}


def test_host_round_updates_keep_originals_and_current_history_across_rejoin_and_revision(tmp_path):
    store, rid, source, path, original, joined = setup(tmp_path)
    resolved = update(joined, source)
    partial = {**update(joined, source, 'partial', 1), 'reason': 'Pricing located; release date still missing.',
               'remaining_question': 'What was the release date?'}
    # A host may write a handoff file; finish_research_round validates and applies it.
    round_path = store.root / 'research' / rid / 'rounds' / '1'
    round_path.mkdir(parents=True, exist_ok=True)
    (round_path / 'handoff.json').write_text(json.dumps({'learnings': [], 'covered': ['announcement'],
                                                       'open_questions': [], 'gap_updates': [resolved, partial]}))
    request = {'action': 'finish_research_round', 'run_id': rid}
    closed = workspace_action(store, request)
    outcome_bytes = (round_path / 'outcome.json').read_bytes()
    assert len(closed['gap_updates']) == 2
    assert workspace_action(store, request)['idempotent']
    research_plan.begin_round(store, rid, target_gap_ids=[partial['gap_id']])
    refreshed = join_scouts(store, [path], run_id=rid)
    assert path.read_bytes() == original
    assert refreshed['gaps'] == ['What was the release date?']
    assert refreshed['gap_history'][0]['gap_id'] == resolved['gap_id']
    assert refreshed['gap_records'][0]['gap_id'] == partial['gap_id']
    assert refreshed['gap_records'][0]['evidence'][0]['source_hash'] == source['hash']
    assert current_research(store, rid, refreshed, register=True) == refreshed
    assert len(read_state(store, rid)['records']) == 2  # remaining_question is not a new gap
    draft = analyst.packet(store, rid, store.root / 'writer', plan={}, research=refreshed)
    base = store.publish(rid, {'title': 'Weekly', 'markdown': 'The product is in public beta.'})
    revision = analyst.packet(store, rid, store.root / 'revision', plan={}, research=joined,
                              base_version=base['id'], feedback=['Keep beta status'])
    assert (draft['root'] / 'research.json').read_bytes() == (revision['root'] / 'research.json').read_bytes()
    # A new round can explicitly reopen, without a positive citation proving absence.
    research_plan.finish_round(store, rid, gap_updates=[{'gap_id': resolved['gap_id'], 'status': 'open',
        'reason': 'The retrieved announcement is a different product; investigate the original question.'}])
    assert len(gap_view(store, rid)['gap_records']) == 2
    assert (round_path / 'outcome.json').read_bytes() == outcome_bytes


def test_gap_updates_reject_bad_evidence_transactionally_and_never_close_by_coverage(tmp_path):
    store, rid, source, path, original, joined = setup(tmp_path)
    # covered and omission of open_questions are not status updates.
    check_handoff(store, rid, {'learnings': [], 'covered': ['Everything covered'], 'open_questions': []})
    assert len(gap_view(store, rid)['gaps']) == 2
    valid = update(joined, source)
    bad = {**update(joined, source, index=1), 'evidence': [{
        'source_id': source['id'], 'locator': 'line 2', 'excerpt': 'Public beta on September 10.'}]}
    with pytest.raises(ValueError, match=r'gap_updates\[1\].*指定位置'):
        research_plan.finish_round(store, rid, gap_updates=[valid, bad])
    assert read_state(store, rid)['updates'] == []
    assert research_plan.frozen(store, rid)['current_round_id']
    foreign = store.add_source('Other task', 'Public beta on September 10.')
    cases = [
        {**valid, 'gap_id': 'gap_other_run'},
        {**valid, 'evidence': [{**valid['evidence'][0], 'source_id': foreign['id']}]},
        {**valid, 'evidence': [{**valid['evidence'][0], 'source_hash': 'obsolete'}]},
        {**valid, 'status': 'partial'},
        {**valid, 'evidence': []},
    ]
    for candidate in cases:
        with pytest.raises(HandoffError):
            check_handoff(store, rid, {'learnings': [], 'gap_updates': [candidate]})
    research_plan.finish_round(store, rid, gap_updates=[valid])
    with pytest.raises(ValueError, match='已关闭轮次'):
        research_plan.finish_round(store, rid, gap_updates=[{'gap_id': valid['gap_id'], 'status': 'open', 'reason': 'late change'}])
    # Changed snapshot bytes cannot silently retain a previous resolution.
    (store.root / source['path']).write_text('Modified release content.')
    view = gap_view(store, rid)
    assert not view['gap_history']
    reopened = next(item for item in view['gap_records'] if item['gap_id'] == valid['gap_id'])
    assert reopened['status'] == 'open' and reopened['validation_error']
    assert read_state(store, rid)['updates'][0]['status'] == 'resolved'


def test_native_handoff_refreshes_packet_and_preserves_closed_round(tmp_path):
    store, rid, source, path, original, joined = setup(tmp_path)
    packet = store.root / 'native' / 'packet'
    packet.mkdir(parents=True)
    (packet.parent / 'research.json').write_text(json.dumps(joined))
    config = {'run_id': rid, 'session_id': 'test', 'packet_root': str(packet),
              '_harness': SimpleNamespace(cancel_requested=lambda _: False)}
    handoff = {'learnings': [], 'covered': [], 'open_questions': [], 'gap_updates': [update(joined, source)]}
    native_orchestrator.save_handoff(store, config, {'handoff': handoff})
    native_orchestrator.save_handoff(store, config, {'handoff': handoff})
    assert len(read_state(store, rid)['updates']) == 1
    saved = json.loads((packet / 'research.json').read_text())
    assert len(saved['gap_history']) == 1 and len(saved['gaps']) == 1
    native_orchestrator.action(store, config, {'request': {'action': 'finish_research_round'}})
    replay = native_orchestrator.save_handoff(store, config, {'handoff': handoff})
    assert json.loads(replay['content'][0]['text'])['idempotent']
    from briefloop.native_roles import ToolError
    with pytest.raises(ToolError, match='已收束'):
        native_orchestrator.save_handoff(store, config, {'handoff': {'learnings': [], 'covered': ['different']}})
    assert path.read_bytes() == original


def test_upgraded_rounds_keep_duplicate_description_ids_usable_without_rewriting_history(tmp_path):
    store, rid, source, path, original, joined = setup(tmp_path)
    plan = research_plan.frozen(store, rid)
    plan['current_round_id'] = None
    plan['rounds'] = {}
    snapshots = {}
    for index, identity in ((1, 'gap_legacy_first'), (2, 'gap_legacy_second')):
        gap = {'id': identity, 'description': 'Missing date', 'round_id': f'old_r{index}', 'round_index': index}
        info = {'status': 'closed', 'index': index, 'gaps': [gap], 'outcome': {'summary': 'legacy', 'gap_ids': [identity]}}
        plan['rounds'][f'old_r{index}'] = info
        outcome_path = store.root / 'research' / rid / 'rounds' / str(index) / 'outcome.json'
        outcome_path.parent.mkdir(parents=True, exist_ok=True)
        outcome_path.write_text(json.dumps(info))
        snapshots[outcome_path] = outcome_path.read_bytes()
    store.set_meta('research_plan:' + rid, plan)
    state = read_state(store, rid)
    assert state['aliases']['gap_legacy_second'] == 'gap_legacy_first'
    assert sum(item['description'] == 'Missing date' for item in state['records'].values()) == 1
    opened = research_plan.begin_round(store, rid, target_gap_ids=['gap_legacy_second'])
    assert opened['index'] == 3
    change = {**update(joined, source), 'gap_id': 'gap_legacy_second'}
    closed = research_plan.finish_round(store, rid, gap_updates=[change])
    assert closed['gap_updates'][0]['gap_id'] == 'gap_legacy_first'
    assert research_plan.finish_round(store, rid, gap_updates=[change])['idempotent']
    view = gap_view(store, rid)
    assert any(item['gap_id'] == 'gap_legacy_first' for item in view['gap_history'])
    current_plan = research_plan.frozen(store, rid)
    for identity in ('old_r1', 'old_r2'):
        assert current_plan['rounds'][identity] == plan['rounds'][identity]
    assert all(path.read_bytes() == content for path, content in snapshots.items())


def test_native_handoff_file_write_and_close_are_serialized_and_write_failure_rolls_back(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    store, rid, source, path, original, joined = setup(tmp_path)
    packet = store.root / 'native' / 'packet'
    packet.mkdir(parents=True)
    (packet.parent / 'research.json').write_text(json.dumps(joined))
    config = {'run_id': rid, 'session_id': 'test', 'packet_root': str(packet),
              '_harness': SimpleNamespace(cancel_requested=lambda _: False)}
    native_orchestrator.save_handoff(store, config, {'handoff': {'learnings': [], 'covered': ['initial']}})
    handoff_path = store.root / 'research' / rid / 'rounds' / '1' / 'handoff.json'
    original_handoff = handoff_path.read_bytes()
    original_save = native_orchestrator._save
    new_handoff = {'learnings': [], 'covered': ['updated'], 'gap_updates': [update(joined, source)]}

    def failed_save(target, value):
        if target == handoff_path:
            raise OSError('disk write failed')
        return original_save(target, value)

    monkeypatch.setattr(native_orchestrator, '_save', failed_save)
    with pytest.raises(OSError, match='disk write failed'):
        native_orchestrator.save_handoff(store, config, {'handoff': new_handoff})
    assert not read_state(store, rid)['updates']
    assert handoff_path.read_bytes() == original_handoff
    entered, release, close_started, closed = (Event() for _ in range(4))

    def paused_save(target, value):
        if target == handoff_path:
            entered.set()
            assert release.wait(3)
        return original_save(target, value)

    def close():
        close_started.set()
        result = research_plan.finish_round(store, rid)
        contents = handoff_path.read_bytes()
        closed.set()
        return result, contents

    monkeypatch.setattr(native_orchestrator, '_save', paused_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(native_orchestrator.save_handoff, store, config, {'handoff': new_handoff})
        assert entered.wait(3)
        closing = pool.submit(close)
        try:
            assert close_started.wait(3)
            assert not closed.wait(0.15)  # close must wait for the atomic handoff write
        finally:
            release.set()
        writing.result(timeout=3)
        result, at_close = closing.result(timeout=3)
    assert len(result['gap_updates']) == 1
    assert handoff_path.read_bytes() == at_close
    assert json.loads(at_close)['covered'] == ['updated']
