import threading
import time
from briefloop.runtime import Worker
from briefloop.store import Store


def wait_for(predicate):
    end=time.monotonic()+5
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.02)
    assert predicate()


def test_report_slots_and_independent_cancellation(tmp_path):
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'auto_learn':False,'max_reports':2})
    worker=Worker(store)
    entered={};lock=threading.Lock();release=threading.Event()
    def generate(job):
        runtime=worker.runtime
        with lock:entered[job['id']]=runtime
        while not release.wait(.02):
            if runtime.cancelled.is_set():raise InterruptedError('cancelled')
        return {}
    worker.generate=generate
    jobs=[store.enqueue('generate',{}) for _ in range(3)]
    worker.thread.start()
    try:
        wait_for(lambda:len(entered)==2)
        assert store.one('jobs',jobs[2]['id'])['status']=='queued'
        assert entered[jobs[0]['id']] is not entered[jobs[1]['id']]
        worker.stop_job(jobs[0]['id'])
        wait_for(lambda:len(entered)==3)
        assert not entered[jobs[1]['id']].cancelled.is_set()
        assert store.one('jobs',jobs[0]['id'])['status']=='cancelled'
        release.set()
        wait_for(lambda:store.one('jobs',jobs[2]['id'])['status']=='complete')
    finally:
        release.set();worker.close()


def test_deep_length_defaults_respect_explicit_user_length():
    from briefloop.models import Requirements
    req=Requirements(title='月报',objective='深度研究',research_tier='deep')
    assert (req.target_words,req.max_words)==(10000,12000)
    explicit=Requirements(title='专题',objective='精简',research_tier='deep',target_words=8000,max_words=9000)
    assert (explicit.target_words,explicit.max_words)==(8000,9000)


def test_long_task_and_full_report_slots_do_not_starve_other_work(tmp_path):
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'auto_learn':False,'max_reports':1})
    worker=Worker(store)
    started=[];gates={};lock=threading.Lock()
    def gate(jid):
        with lock:return gates.setdefault(jid,threading.Event())
    def blocking(job):
        started.append(job['id'])
        if not gate(job['id']).wait(10):raise AssertionError('test did not release '+job['id'])
        return {}
    worker.generate=blocking;worker.assess=blocking
    task=store.enqueue('assess',{})
    report=store.enqueue('generate',{})
    worker.thread.start()
    try:
        # A: a long non-report task must not keep a free report slot idle.
        wait_for(lambda:task['id'] in started and report['id'] in started)
        # B: with the only report slot taken, a queued report must not hide later work.
        waiting=store.enqueue('generate',{})
        later=store.enqueue('assess',{})
        time.sleep(.6)  # non-report tasks stay serial on the shared runtime
        assert later['id'] not in started
        gate(task['id']).set()
        wait_for(lambda:later['id'] in started)
        assert waiting['id'] not in started and store.one('jobs',waiting['id'])['status']=='queued'
        gate(report['id']).set()
        wait_for(lambda:waiting['id'] in started)
        for job in (later,waiting):gate(job['id']).set()
        for job in (task,report,waiting,later):
            wait_for(lambda job=job:store.one('jobs',job['id'])['status']=='complete')
    finally:
        for event in list(gates.values()):event.set()
        worker.close()
