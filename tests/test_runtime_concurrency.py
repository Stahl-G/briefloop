"""Queued generation retains concurrency when workspace preferences change."""
import json
import pytest
from briefloop.store import Store
from briefloop.runtime import Worker
from briefloop.research_plan import freeze, mark_protocol


@pytest.mark.parametrize('quality,plan_parallel,legacy,expected', [(False, None, False, 3), (True, None, False, 2), (True, 8, False, 3), (False, None, True, 7)])
def test_generation_uses_queued_limit_and_frozen_plan(tmp_path, quality, plan_parallel, legacy, expected):
    store = Store(tmp_path/'workspace')
    store.set_meta('settings', {**store.settings(), 'max_parallel': 3})
    source = store.add_source('test', 'test evidence')
    run = store.create_run({'title': 'test', 'objective': 'test concurrency'}, [source['id']])
    job = store.enqueue('generate', {'run_id': run['id']})
    if legacy:
        payload = json.loads(job['payload'])
        payload.pop('max_parallel', None)
        job['payload'] = json.dumps(payload)
        with store.tx() as connection:
            connection.execute('UPDATE jobs SET payload=? WHERE id=?', (job['payload'], job['id']))
    if quality:
        mark_protocol(store, run['id'])
        if plan_parallel:
            freeze(store, run['id'], structure={'parallel': plan_parallel})
    store.set_meta('settings', {**store.settings(), 'max_parallel': 7})
    worker = Worker(store)

    class CaptureRuntime:
        def execute(self, job, prompt, folder, publish):
            packet = json.loads((folder/'input.json').read_text())
            assert packet['max_parallel'] == expected
            assert len(packet['scout_slots']) == expected
            assert f'上限 {expected}；' in prompt
            if quality:
                assert packet['research_plan']['frozen_runtime']['max_parallel'] == 3
            raise RuntimeError('captured generation')

    worker.runtime = CaptureRuntime()
    with pytest.raises(RuntimeError, match='captured generation'):
        worker.generate(job, score=False)
