"""Queue backoff conserves idle work without losing committed admissions."""
import threading
import time

from briefloop.runtime import Worker
from briefloop.store import Store


def test_backoff_wakes_on_admission_and_close(tmp_path):
    store=Store(tmp_path)
    worker=Worker(store)
    samples=[];received=threading.Event()
    def consume():
        for jobs in worker._queued(0,"SELECT * FROM jobs WHERE status='queued'"):
            samples.append(time.monotonic())
            if jobs:received.set()
    thread=threading.Thread(target=consume)
    thread.start()
    try:
        time.sleep(1.8)
        assert 2<=len(samples)<=3  # Normally 0, .5, 1.5; allow a loaded runner.
        store.enqueue('source_refresh',{})
        assert received.wait(.5), 'same-service admission waited for idle polling'
    finally:
        worker.stopping.set();worker.wake();thread.join(1)
        assert not thread.is_alive()


def test_fallback_observes_another_store_instance(tmp_path):
    store=Store(tmp_path);other=Store(tmp_path)
    worker=Worker(store);worker.IDLE_POLL_SECONDS=.1
    received=threading.Event()
    def consume():
        for jobs in worker._queued(2,"SELECT * FROM jobs WHERE status='queued'"):
            if jobs:
                received.set();return
    thread=threading.Thread(target=consume);thread.start()
    try:
        other.enqueue('source_refresh',{})
        assert received.wait(1)
    finally:
        worker.stopping.set();worker.wake();thread.join(1)
        assert not thread.is_alive()
