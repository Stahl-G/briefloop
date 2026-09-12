import json
import threading

import pytest

from briefloop.demo import create_demo
from briefloop.store import Store
from briefloop.release import eligibility
from briefloop.export_jobs import enqueue_export, generate_word, output_path


def test_demo_is_saved_editable_unreviewed_and_exports_without_a_model(tmp_path):
    store = Store(tmp_path)
    result = create_demo(store)
    brief = store.one('briefs', result['version_id'])
    assert brief['author'] == 'example'
    assert not store.rows('SELECT * FROM assessments')
    assert not store.rows('SELECT * FROM jobs')
    assert not store.settings()['auto_learn']
    assert store.settings()['model_selection_required']
    with pytest.raises(ValueError, match='选择'):
        store.enqueue('review', {'version_id': brief['id']})
    assert not eligibility(store, brief['id'])['eligible']
    assert create_demo(store) == result
    edited = store.revise(brief['id'], brief['markdown'].replace('120', '121'))
    assert store.one('briefs', brief['id'])['markdown'] == brief['markdown']
    assert edited['parent_id'] == brief['id']
    job = enqueue_export(store, edited['id'])
    generate_word(store, job, threading.Event())
    assert output_path(store, job).is_file()
    reopened = Store(tmp_path)
    assert reopened.meta('demo')['version_id'] == brief['id']
    assert reopened.one('briefs', edited['id'])['markdown'] == edited['markdown']
    explicit = store.enqueue('review', {'version_id': brief['id'],
        'runtime': {'model': 'explicit-review-model', 'reasoning_effort': 'high'}})
    assert json.loads(explicit['payload'])['runtime']['model'] == 'explicit-review-model'


def test_demo_does_not_change_an_existing_workspace(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Existing', 'Keep this')
    settings = store.settings()
    with pytest.raises(ValueError, match='空工作区'):
        create_demo(store)
    assert store.settings() == settings
    assert store.source_text(source['id']) == 'Keep this'


def test_demo_retry_after_partial_failure_and_parallel_requests_share_one_import(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    store = Store(tmp_path)
    def interrupted(*args, **kwargs):
        raise OSError('Synthetic interruption')
    monkeypatch.setattr(store, 'publish', interrupted)
    with pytest.raises(OSError):
        create_demo(store)
    # Resume with fresh Store instances as a server restart would.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: create_demo(Store(tmp_path)), range(8)))
    assert all(result == results[0] for result in results)
    for table in ('sources', 'runs', 'briefs'):
        assert len(store.rows('SELECT * FROM '+table)) == 1
