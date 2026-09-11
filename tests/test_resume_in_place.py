"""Resume continues the same task; it must not spawn a new attempt on every click."""
from briefloop.runtime import Worker
from briefloop.store import Store


def test_resume_requeues_the_same_job_regardless_of_current_settings(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Source', 'content')
    run = store.create_run({'title': 'Test', 'objective': 'summarize'}, [source['id']])
    job = store.enqueue('generate', {'run_id': run['id'],
                                     'runtime': {'model': 'old-model'}, 'agent_backend': 'codex'})
    store.update_job(job['id'], 'interrupted', error='stopped')
    # The workspace settings no longer match the frozen job; resume must still be in place.
    store.set_meta('settings', {**store.settings(), 'agent_backend': 'opencode', 'model': 'new-model'})
    resumed = Worker(store).resume(job['id'])
    assert resumed['id'] == job['id'] and resumed['status'] == 'queued'
    assert len(store.rows("SELECT id FROM jobs WHERE kind='generate'")) == 1
