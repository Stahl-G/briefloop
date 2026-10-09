"""Exercise the actual product route/storage with a local runtime, no model calls."""
import hashlib
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'native-engine'))
import experiment_ab
import briefloop.review_capability as capability
import briefloop.runtime as product_runtime
from briefloop.store import Store, dump


def setup(tmp_path):
    store = Store(tmp_path / 'ws')
    store.set_meta('settings', {**store.settings(), 'auto_learn': False, 'company_context_enabled': False})
    source = store.add_source('虚构季度资料', '本季度交付 100 台。')
    run = store.create_run({'title': '合成内部简报', 'objective': '列出季度交付数据。',
                            'writing_mode': 'internal_report', 'fact_check': False}, [source['id']])
    brief = store.publish(run['id'], {'title': '合成内部简报', 'markdown': '## 数据\n本季度交付 100 台。'})
    job = store.enqueue('assess', {'version_id': brief['id'], 'agent_backend': 'briefloop-native',
                                  'runtime': {'model': 'synthetic/controlled-local-no-api'}})
    return store, brief, job


class LocalRuntime:
    cancelled = threading.Event()
    prompt = None

    def __init__(self, *, fail=False):
        self.fail = fail

    def execute(self, job, prompt, folder, **kwargs):
        self.prompt = prompt
        if self.fail:
            raise RuntimeError('controlled runtime failure')
        pack = json.loads((folder / 'input.json').read_text())
        value = {'brief_hash': pack['brief']['hash'], 'status': 'complete',
                 'summary': '本地合成评价', 'overall': '达到要求',
                 'evidence': 4, 'coverage': 4, 'analysis': 4, 'expression': 4,
                 'checks': [{**item, 'status': 'met', 'reason': '合成资料完整'}
                            for item in pack['assessment_checks']]}
        from briefloop.native_roles import run_tool
        result = run_tool(self.store,
                          {'native_role': 'evaluator', 'packet_root': str(folder / 'packet'),
                           'version_id': pack['brief']['id']},
                          'submit_assessment', {'assessment': value})
        assert result['ok']
        return {'status': 'complete', 'controlled_runtime': True}


def test_evaluator_route_is_saved_and_product_availability_restored(tmp_path, monkeypatch):
    store, brief, job = setup(tmp_path)
    available = lambda *args, **kwargs: True
    monkeypatch.setattr(capability, 'review_available', available)
    original_prompt = product_runtime.assessment_prompt
    runtime = LocalRuntime()
    runtime.store = store
    folder = tmp_path / 'evaluation'
    result = experiment_ab.evaluate(store, runtime, job, brief['id'], folder)
    receipt = json.loads((folder / 'evaluator-route.json').read_text())
    assert result['status'] == 'complete'
    assert receipt['route'] == 'evaluator'
    assert receipt['prompt_sha256'] == hashlib.sha256(runtime.prompt.encode()).hexdigest()
    assert not (folder / 'review').exists()
    assert (folder / 'packet' / 'input.json').is_file()
    assert receipt['packet_input_sha256'] == hashlib.sha256((folder / 'packet' / 'input.json').read_bytes()).hexdigest()
    assert receipt['assessment_sha256'] == hashlib.sha256((folder / 'assessment.json').read_bytes()).hexdigest()
    assert store.rows('SELECT data FROM assessments WHERE version_id=?', (brief['id'],))
    assert capability.review_available is available
    assert product_runtime.assessment_prompt is original_prompt


def test_failed_evaluator_retains_route_receipt_and_restores_product(tmp_path, monkeypatch):
    store, brief, job = setup(tmp_path)
    available = lambda *args, **kwargs: True
    monkeypatch.setattr(capability, 'review_available', available)
    original_prompt = product_runtime.assessment_prompt
    folder = tmp_path / 'evaluation'
    with pytest.raises(RuntimeError, match='controlled runtime failure'):
        experiment_ab.evaluate(store, LocalRuntime(fail=True), job, brief['id'], folder)
    receipt = json.loads((folder / 'evaluator-route.json').read_text())
    assert receipt['route'] == 'evaluator'
    assert receipt['prompt_sha256']
    assert not store.rows('SELECT data FROM assessments WHERE version_id=?', (brief['id'],))
    assert capability.review_available is available
    assert product_runtime.assessment_prompt is original_prompt
