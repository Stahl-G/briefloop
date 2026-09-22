"""Frozen research conditions must not drift when switching the Scout backend."""
import importlib
import json
from pathlib import Path

import pytest

from briefloop import scout
from briefloop.agent_prompts import system_prompt
from briefloop.search_policy import allowed
from briefloop.store import Store


@pytest.fixture
def experiment(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'native-engine'))
    return importlib.import_module('experiment_scout')


def test_legacy_search_snapshot_is_not_expanded_by_current_defaults(experiment):
    saved = {'search_provider': 'tavily', 'requirements': {'allow_web': True}}
    assert allowed(experiment.frozen_policy(saved)) == ['tavily']
    experiment.validate_comparison(saved, ['briefloop-native', 'opencode'])
    saved['search_policy'] = {'primary_provider': 'tavily', 'native_search_enabled': True}
    before = json.dumps(saved, sort_keys=True)
    with pytest.raises(ValueError, match='冻结策略包含宿主自带搜索'):
        experiment.validate_comparison(saved, ['briefloop-native', 'opencode'])
    assert json.dumps(saved, sort_keys=True) == before
    experiment.validate_comparison(saved, ['opencode'])
    experiment.validate_comparison({**saved, 'requirements': {'allow_web': False}}, ['briefloop-native', 'opencode'])


def test_backends_receive_same_research_inputs_without_native_protocol_leaking(experiment, tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('Same frozen source', 'Revenue 12.\nFootnote: same period.')
    run = store.create_run({'title': 'T', 'objective': 'Explain the change', 'allow_web': True}, [source['id']])
    store.enqueue('generate', {'run_id': run['id'], 'agent_backend': 'opencode', 'runtime': {'model': 'fake/model'},
                              'search_policy': {'primary_provider': 'tavily', 'native_search_enabled': False}})
    assignment = {'slot_id': 'scout-1', 'theme': 'Revenue'}
    native = experiment.comparison_conditions(store, run['id'], assignment, None, 'briefloop-native')
    host = experiment.comparison_conditions(store, run['id'], assignment, None, 'opencode')
    assert native['strategy_inputs_sha256'] == host['strategy_inputs_sha256']
    assert native['effective_search_channels'] == host['effective_search_channels'] == ['tavily']
    assert native['read_max_chars'] == host['read_max_chars'] == 60000
    assert native['evidence_transport'] != host['evidence_transport']
    task = scout.task(store, run['id'], assignment)
    folder = store.root / 'host'; folder.mkdir()
    prompt = scout.host_prompt(store, task, folder, 'opencode')
    assert '--max-chars 60000' in prompt
    assert 'record_evidence' not in prompt + task['contract'] + (folder / 'SKILL.md').read_text()
    assert (folder / 'scout-contract.md').read_text() == task['contract']
    assert '先 source_grep' not in system_prompt('scout')['text'] + scout.native_prompt(task)
    changed = experiment.comparison_conditions(store, run['id'], {**assignment, 'theme': 'Costs'}, None, 'opencode')
    assert changed['strategy_inputs_sha256'] != host['strategy_inputs_sha256']


def test_changed_conditions_stop_before_model_dispatch(experiment, tmp_path, monkeypatch):
    import shutil
    store = Store(tmp_path / 'ws')
    source = store.add_source('Frozen', 'Same text.')
    run = store.create_run({'title': 'T', 'objective': 'Read', 'allow_web': False}, [source['id']])
    job = store.enqueue('generate', {'run_id': run['id']})
    folder = store.root / 'jobs' / job['id']; folder.mkdir()
    (folder / 'plan.json').write_text(json.dumps({'scout_assignments': [{'slot_id': 'scout-1'}]}))
    (folder / 'input.json').write_text(json.dumps({'search_provider': 'tavily', 'requirements': {'allow_web': False}}))
    def no_dispatch(*args, **kwargs):
        pytest.fail('Changed comparison conditions must not call the model')
    monkeypatch.setattr(experiment.InteractiveRuntime, 'execute', no_dispatch)
    result = experiment.run_leg(store.root, job['id'], 'scout-1', 'briefloop-native', 'fake/model', 'low', None, 0,
                                expected_conditions={'strategy_inputs_sha256': 'different', 'effective_search_channels': []})
    try:
        assert result['status'] == 'failed'
        assert '对照条件与首臂不一致，未调用模型' in result['error']
    finally:
        shutil.rmtree(result['work'])
