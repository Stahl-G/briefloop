"""Production tool wiring and bounded empty-round recovery; no billed models."""
import json
from types import SimpleNamespace
import pytest
from briefloop import duckduckgo, native_orchestrator, research_budget, research_plan, sources
from briefloop.native_roles import run_tool
from briefloop.runtime import generation_prompt
from briefloop.scout_coverage import declare
from briefloop.store import Store


def setup(tmp_path, **requirements):
    store = Store(tmp_path)
    policy = {'primary_provider': 'duckduckgo', 'supplemental_providers': [], 'native_search_enabled': False}
    store.update_settings({'agent_backend': 'briefloop-native', 'model': 'fixture/model',
                           'search_provider': 'duckduckgo', 'search_policy': policy})
    initial = [store.add_source('Local', 'Local original.')['id']] if requirements.get('allow_web') is False else []
    run = store.create_run({'title': 'Release note', 'objective': 'What changed?',
        'key_questions': ['What changed?'], 'research_strategy': 'goal_driven', 'allow_web': True,
        'fact_check': False, 'research_budget': {'search_requests': 1, 'candidate_urls': 2, 'source_pages': 1},
        'search_policy': policy, **requirements}, initial, research_protocol='quality_v1', agent_backend='briefloop-native')
    job = store.enqueue('generate', {'run_id': run['id'], 'agent_backend': 'briefloop-native', 'runtime': {'model': 'fixture/model'}})
    store.update_job(job['id'], 'running')
    plan = research_plan.freeze(store, run['id'])
    folder = tmp_path/'generation';folder.mkdir()
    prompt = generation_prompt(store, run, folder, backend='briefloop-native')
    config, _ = native_orchestrator.prepare(store, {**job, 'allow_web': requirements.get('allow_web', True)}, folder, prompt)
    config.update(packet_root=str(folder/'packet'), native_role='orchestrator', session_id='synthetic',
                  _harness=SimpleNamespace(cancel_requested=lambda _: False))
    declare(store, run['id'], [])
    return store, run['id'], config, plan


def call(store, config, tool, args):
    result = run_tool(store, config, tool, args)
    assert result['ok'], result
    return json.loads(result['content'][0]['text'])


def open_handoff(store, config):
    return call(store, config, 'save_research_handoff', {'handoff': {'learnings': [], 'question_coverage': [
        {'question_id': 'q1', 'status': 'open', 'reason': 'No original available',
         'remaining_question': 'What changed?', 'evidence': []}]}})


def test_native_direct_web_uses_shared_budget_and_source_handoff_without_scout(tmp_path, monkeypatch):
    store, rid, config, plan = setup(tmp_path)
    monkeypatch.setattr(duckduckgo, 'call_search', lambda *a, **k: (
        {'results': [{'title': 'Original', 'url': 'https://example.test/release'}]}, b'{}', None, None))
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *a, **k: (b'Revenue increased by 5%.', 'text/plain', 'utf-8'))
    query = {'provider': 'duckduckgo', 'query': 'official release', 'purpose': 'primary', 'reason': 'Read the original'}
    assert call(store, config, 'web_search', query)['status'] == 'ok'
    receipt = call(store, config, 'add_url', {'url': 'https://example.test/release'})
    sid = receipt['source_id'];assert receipt['status'] == 'ready'
    assert 'Revenue increased by 5%.' in run_tool(store, config, 'source_read', {'source_id': sid})['content'][0]['text']
    assert research_budget.snapshot(store, rid)['used']['search_requests'] == 1
    assert research_budget.snapshot(store, rid)['used']['source_pages'] == 1
    # A Scout shares the same run's exhausted budget, rather than getting a new pool.
    assert call(store, {**config, 'native_role': 'scout'}, 'web_search', query)['status'] == 'budget_exhausted'
    call(store, config, 'save_research_handoff', {'handoff': {'learnings': [], 'question_coverage': [
        {'question_id': 'q1', 'status': 'answered', 'reason': 'Original contains the change',
         'evidence': [{'source_id': sid, 'locator': 'line 1', 'excerpt': 'Revenue increased by 5%.'}]}]}})
    call(store, config, 'workspace_action', {'request': {'action': 'finish_research_round', 'summary': 'Original acquired and scope answered'}})
    from pathlib import Path
    research = json.loads((Path(config['packet_root'])/'research.json').read_text())
    note = next(n for n in research['retrieval_notes'] if n.get('kind') == 'research_round_closeout')
    assert note['question_coverage'][0]['evidence'][0]['source_id'] == sid
    assert research_plan.require_writing_closeout(store, rid)
    from briefloop.scout_coverage import view
    assert not view(store, rid)['scout_execution']


@pytest.mark.parametrize('choice', [dict(allow_web=False), dict(research_strategy='guided')])
def test_direct_search_does_not_expand_offline_or_guided_roles(tmp_path, choice):
    store, _, config, _ = setup(tmp_path, **choice)
    assert not run_tool(store, config, 'web_search', {})['ok']
    assert not run_tool(store, config, 'add_url', {'url': 'https://example.test'})['ok']
    assert not run_tool(store, {**config, 'native_role': 'evaluator'}, 'web_search', {})['ok']


def test_empty_web_round_requires_separate_reason_but_preserves_unknown(tmp_path):
    store, rid, config, _ = setup(tmp_path)
    open_handoff(store, config)
    with pytest.raises(research_plan.AdmissionError, match='early_stop_reason'):
        research_plan.finish_round(store, rid, summary='Done')
    assert research_plan.frozen(store, rid)['current_round_id']
    research_plan.finish_round(store, rid, summary='Limited draft only',
        early_stop_reason='No configured provider can reach the original; no claim is verified.')
    progress = research_plan.status(store, rid)['goal_progress']
    assert progress['questions'][0]['status'] == 'open'
    assert research_plan.require_writing_closeout(store, rid)


def test_v1_frozen_contract_replays_without_new_stop_obligation(tmp_path):
    store, rid, config, plan = setup(tmp_path)
    # The exact old contract shape and fingerprint are retained across an upgrade.
    plan['goal_contract']['version'] = 1
    for info in plan['rounds'].values():info.pop('source_ids_at_start', None)
    keys = ['research_protocol', 'run_id', 'owner_generate_job_id', 'authorized_budget', 'structure',
            'budget', 'preset_id', 'frozen_runtime', 'research_strategy', 'goal_contract']
    plan['plan_fingerprint'] = research_plan._fingerprint({k: plan[k] for k in keys})
    store.set_meta('research_plan:' + rid, plan)
    assert research_plan.freeze(store, rid) == plan
    open_handoff(store, config)
    research_plan.finish_round(store, rid, summary='Old contract permits limited draft')


def test_known_url_can_supply_evidence_without_search_and_next_round_has_own_boundary(tmp_path, monkeypatch):
    store, rid, config, _ = setup(tmp_path, research_budget={'search_requests': 3, 'candidate_urls': 3, 'source_pages': 3})
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *a, **k: (b'Original data.', 'text/plain', 'utf-8'))
    assert call(store, config, 'add_url', {'url': 'https://example.test/original'})['status'] == 'ready'
    open_handoff(store, config)
    research_plan.finish_round(store, rid, summary='Directly read original; missing details remain open')
    research_plan.begin_round(store, rid)
    declare(store, rid, [])
    open_handoff(store, config)
    with pytest.raises(research_plan.AdmissionError, match='early_stop_reason'):
        research_plan.finish_round(store, rid, summary='No activity in new round')
