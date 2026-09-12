import json
import pytest
from briefloop.fast_mode import capability
from briefloop.models import Settings, runtime_fields
from briefloop.harness import HarnessManager
from briefloop.store import Store
from briefloop.runtime import stage_job
from test_harness import RPC, until


class Host(RPC):
    def request(self, method, params):
        if method == 'config/read':
            return {'config': {'model_provider': 'openai', 'service_tier': None}}
        if method == 'account/read':
            return {'account': {'type': 'chatgpt', 'planType': 'pro'}}
        if method == 'model/list':
            return {'data': [{'model': 'test', 'serviceTiers': [{'id': 'priority'}]}]}
        return super().request(method, params)


def test_capability_separates_catalog_from_account_and_provider(tmp_path):
    host = Host()
    result = capability(host, {'model': 'test'}, tmp_path, environment={'HTTPS_PROXY':'http://proxy'})
    assert result['official_connection'] and result['fast_supported']
    assert result['account_availability'] == 'unknown' and not result['enabled']
    custom = capability(host, {'model': 'test', 'model_provider': 'custom'}, tmp_path, environment={})
    assert not custom['official_connection'] and not custom['enabled']
    override = capability(host, {'model': 'test'}, tmp_path, environment={'OPENAI_BASE_URL':'https://example.org/v1'})
    assert not override['official_connection']


def test_tiers_freeze_by_role_and_filter_other_backends(tmp_path):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'service_tier':'fast',
        'role_models': {'evaluator': {'model':'test', 'service_tier':'default'}}})
    job = store.enqueue('learn', {})
    store.set_meta('settings', {**store.settings(), 'service_tier':'default'})
    assert json.loads(job['payload'])['runtime']['service_tier'] == 'fast'
    assert json.loads(stage_job(store, job, 'evaluator')['payload'])['runtime']['service_tier'] == 'default'
    for backend, model in [('claude','test'), ('opencode','openai/test')]:
        assert 'service_tier' not in runtime_fields({'model':model, 'service_tier':'fast'}, backend)
    with pytest.raises(ValueError): Settings(service_tier='priority')


def test_off_and_explicit_inherit_clear_persistent_turn_override(tmp_path, monkeypatch):
    # A host entitlement response is injected only at the test boundary. The
    # production probe cannot invent this permission from model names or plans.
    monkeypatch.setattr('briefloop.fast_mode.capability', lambda *a, **kw: {'enabled':True})
    manager = HarnessManager(Store(tmp_path), Host)
    sid = manager.create_session(runtime={'model':'test', 'service_tier':'fast'})['id']
    manager.send(sid, 'one')
    until(lambda: manager.snapshot(sid)['session']['turn_id'] == 'turn1')
    for index, tier in enumerate(('default', None), 2):
        manager.handle_notification({'method':'turn/completed','params':{'threadId':'t1','turn':{'id':'turn'+str(index-1),'status':'completed'}}})
        manager.send(sid, str(index), runtime={'service_tier':tier})
        until(lambda: manager.snapshot(sid)['session']['turn_id'] == 'turn'+str(index))
    turns = [p for m,p in manager.client.calls if m == 'turn/start']
    assert [p['serviceTier'] for p in turns] == ['priority','default','default']
    assert all(p['effort'] == 'high' for p in turns)
    manager.close()


def test_unknown_entitlement_rejects_before_turn(tmp_path):
    manager = HarnessManager(Store(tmp_path), Host)
    sid = manager.create_session(runtime={'model':'test','service_tier':'fast'})['id']
    manager.send(sid, 'one')
    until(lambda: manager.snapshot(sid)['session']['status'] == 'failed')
    assert not any(m in ('thread/start','turn/start') for m,p in manager.client.calls)
    manager.close()
