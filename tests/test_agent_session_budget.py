"""One workspace ceiling on concurrent agent sessions (#728), without deadlocking reports on their reviews."""
import json
import threading
import time

from briefloop.model_budget import ModelBudget
from briefloop.runtime import Worker
from briefloop.store import Store, now


def _job(store, jid, kind, status='queued', payload=None):
    with store.tx() as c:
        c.execute('INSERT INTO jobs(id,kind,status,payload,created,updated) VALUES(?,?,?,?,?,?)',
                  (jid, kind, status, json.dumps(payload or {}), now(), now()))
    return store.one('jobs', jid)


def _wait(predicate, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.02)
    return predicate()


def test_budget_holds_releases_and_grows_only_into_free_sessions():
    budget = ModelBudget(lambda: 5)
    assert budget.reserve('report', 2) and budget.reserve('task', 1)
    assert not budget.reserve('other', 3)
    assert budget.grow('report', 1 + 4) == 4  # wanted four Scouts, two sessions were free
    assert budget.free() == 0 and budget.snapshot()['peak'] == 5
    budget.release('report')
    assert budget.free() == 4 and budget.snapshot()['held'] == {'task': 1}


def test_dispatch_waits_for_sessions_and_scouts_shrink_to_what_is_free(tmp_path):
    store = Store(tmp_path)
    store.update_settings({'max_reports': 4, 'max_agent_sessions': 4})
    worker = Worker(store)
    first, second, task = _job(store, 'g1', 'generate'), _job(store, 'g2', 'generate'), _job(store, 't1', 'learn')
    assert worker._next_runnable([first, second, task])['id'] == 'g1'
    worker.budget.reserve('g1', 2)
    worker._generation_jobs['g1'] = (None, None)
    # Two sessions left: the next report still fits, but its Scouts only get what remains.
    assert worker._next_runnable([second, task])['id'] == 'g2'
    worker.budget.reserve('g2', 2)
    worker._generation_jobs['g2'] = (None, None)
    assert worker._next_runnable([task]) is None
    assert worker._scout_budget(first)(4) == 1
    worker.budget.release('g2')
    assert worker._scout_budget(first)(4) == 3
    # A learning trial runs inline; its Scouts count against the learning job.
    worker.budget.release('g1')
    worker.budget.reserve('t1', 1)
    trial = _job(store, 'trial', 'generate', 'running', {'inline_owner_job_id': 't1'})
    assert worker._scout_budget(trial)(2) == 2 and worker.budget.snapshot()['held'] == {'t1': 3}


def test_a_report_review_starts_on_a_full_budget_but_a_separate_review_waits(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.update_settings({'max_reports': 4, 'max_agent_sessions': 2})
    started, release = [], threading.Event()

    class Runtime:
        cancelled = threading.Event()
        def cancel(self): self.cancelled.set()

    def review(store_, runtime, job, version_id, folder):
        started.append(job['id'])
        release.wait(5)
        return {}

    monkeypatch.setattr('briefloop.review.run_review', review)
    worker = Worker(store, review_runtime_factory=Runtime)
    monkeypatch.setattr(worker, 'folder', lambda job: tmp_path)
    _job(store, 'report', 'generate', 'running')
    worker.budget.reserve('report', 2)  # the report waits for its review on a full budget
    _job(store, 'own', 'review', payload={'version_id': 'v1', 'parent_job_id': 'report'})
    _job(store, 'separate', 'review', payload={'version_id': 'v2'})
    thread = threading.Thread(target=worker.review_loop, daemon=True)
    thread.start()
    try:
        assert _wait(lambda: 'own' in started)
        time.sleep(.3)
        assert 'separate' not in started and store.one('jobs', 'separate')['status'] == 'queued'
        worker.budget.release('report')
        worker.wake()
        assert _wait(lambda: 'separate' in started)
    finally:
        release.set()
        worker.stopping.set()
        worker.wake()
        thread.join(3)
    assert _wait(lambda: worker.budget.snapshot()['held'] == {})
