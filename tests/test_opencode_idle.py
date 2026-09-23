"""Idle metadata discovery must not leave a host running or kill active work."""
import threading
import time
import pytest
from briefloop.opencode_harness import OpencodeHarness
from briefloop.store import Store

class Client:
    def __init__(self, root):
        self.closed=False
        self.entered=threading.Event();self.release=threading.Event()
    def providers(self, directory=None):
        return {'providers':[{'id':'p','models':{'m':{'name':'Model'}}}]}
    def provider_settings(self):
        self.entered.set();self.release.wait(2)
        return {'ok':True}
    def messages(self, sid, directory=None):return [{'session':sid}]
    def close(self):self.closed=True

def expire(h):
    with h._lock:h._client_used_at=time.monotonic()-h.CLIENT_IDLE_SECONDS-1
    h._reap_idle_client(h._idle_epoch)

def test_idle_releases_host_preserves_cache_and_reopens_saved_session(tmp_path):
    clients=[]
    def factory(root):
        c=Client(root);clients.append(c);return c
    h=OpencodeHarness(Store(tmp_path),factory)
    try:
        expected=h.list_models();first=h.client
        expire(h)
        assert first.closed and h.client is None
        assert h.list_models()==expected and h.client is None
        assert h._client().messages('persisted-session')==[{'session':'persisted-session'}]
        assert len(clients)==2 and h.client is clients[1]
    finally:h.close()

def test_directory_request_lease_and_busy_turn_block_idle_cleanup(tmp_path):
    h=OpencodeHarness(Store(tmp_path),Client)
    try:
        c=h._client();thread=threading.Thread(target=c.provider_settings);thread.start()
        assert c.entered.wait(1)
        expire(h);assert not c.closed and h.client is c
        c.release.set();thread.join(2);assert not thread.is_alive()
        h._busy.add('review-with-active-scout')
        expire(h);assert not c.closed
        h._busy.clear();h._configuring=True
        expire(h);assert not c.closed
        h._configuring=False
        expire(h);assert c.closed
    finally:h.close()

def test_stale_handle_and_timer_cannot_close_replacement(tmp_path):
    h=OpencodeHarness(Store(tmp_path),Client)
    try:
        old=h._client();epoch=h._idle_epoch
        expire(h);assert old.closed
        # A route may retain a handle just before its lease starts.
        assert old.messages('saved')==[{'session':'saved'}]
        replacement=h.client
        h._reap_idle_client(epoch)
        assert replacement is not old and not replacement.closed
    finally:h.close()

def test_actual_timer_reclaims_after_grace(tmp_path):
    h=OpencodeHarness(Store(tmp_path),Client);h.CLIENT_IDLE_SECONDS=.04
    try:
        c=h._client()
        deadline=time.monotonic()+2
        while not c.closed and time.monotonic()<deadline:time.sleep(.01)
        assert c.closed and h.client is None
    finally:h.close()


def test_failed_idle_close_keeps_host_for_concurrent_request(tmp_path, caplog):
    clients=[]

    class FailsWhileRequestWaits(Client):
        def __init__(self, root):
            super().__init__(root)
            self.close_entered=threading.Event()
            self.allow_failure=threading.Event()
            self.close_calls=0

        def close(self):
            self.close_calls+=1
            if self.close_calls==1:
                self.close_entered.set()
                assert self.allow_failure.wait(2)
                raise OSError('owned process tree remains alive')
            super().close()

    def factory(root):
        client=FailsWhileRequestWaits(root);clients.append(client);return client

    h=OpencodeHarness(Store(tmp_path),factory)
    c=h._client()
    requests=[];errors=[];request_started=threading.Event()

    def request_client():
        request_started.set()
        try:requests.append(h._client())
        except Exception as exc:errors.append(exc)

    reaper=threading.Thread(target=expire,args=(h,))
    requester=threading.Thread(target=request_client)
    try:
        with caplog.at_level('ERROR',logger='briefloop.opencode_harness'):
            reaper.start()
            assert c.close_entered.wait(2)
            requester.start()
            assert request_started.wait(2)
            assert h.client is c and len(clients)==1
            c.allow_failure.set()
            reaper.join(2);requester.join(2)
            assert not reaper.is_alive() and not requester.is_alive()
            assert not errors and requests==[c]
            assert h.client is c and not c.closed and len(clients)==1
            assert 'Opencode idle cleanup failed; will retry' in caplog.text
    finally:
        c.allow_failure.set()
        reaper.join(2)
        if requester.ident:requester.join(2)
        h.close()


def test_idle_timer_retries_failed_process_cleanup(tmp_path, caplog):
    class FailsOnce(Client):
        def __init__(self, root):
            super().__init__(root)
            self.close_calls=0
            self.retry_entered=threading.Event()
            self.allow_retry=threading.Event()
            self.closed_event=threading.Event()

        def close(self):
            self.close_calls+=1
            if self.close_calls==1:
                raise OSError('owned process tree remains alive')
            self.retry_entered.set()
            assert self.allow_retry.wait(2)
            super().close()
            self.closed_event.set()

    h=OpencodeHarness(Store(tmp_path),FailsOnce)
    h.CLIENT_IDLE_SECONDS=.04
    c=h._client()
    try:
        with caplog.at_level('ERROR',logger='briefloop.opencode_harness'):
            assert c.retry_entered.wait(2)
            assert h.client is c and not c.closed
            assert 'Opencode idle cleanup failed; will retry' in caplog.text
            c.allow_retry.set()
            assert c.closed_event.wait(2)
            deadline=time.monotonic()+2
            while h.client is not None and time.monotonic()<deadline:time.sleep(.01)
            assert h.client is None and c.close_calls==2
    finally:
        c.allow_retry.set()
        h.close()


def test_explicit_close_failure_retains_client_for_retry(tmp_path):
    class FailsOnce(Client):
        def __init__(self, root):
            super().__init__(root)
            self.close_calls=0

        def close(self):
            self.close_calls+=1
            if self.close_calls==1:
                raise OSError('owned process tree remains alive')
            super().close()

    clients=[]
    def factory(root):
        client=FailsOnce(root);clients.append(client);return client

    h=OpencodeHarness(Store(tmp_path),factory)
    c=h._client()
    with pytest.raises(OSError,match='process tree'):
        h.close()
    assert h.client is c and h._closed and not c.closed
    h.close()
    assert h.client is None and c.closed and c.close_calls==2 and clients==[c]


def test_explicit_close_waits_for_client_startup(tmp_path):
    started=threading.Event();allow_start=threading.Event()
    close_entered=threading.Event()
    clients=[];obtained=[];errors=[]

    def factory(root):
        started.set()
        assert allow_start.wait(2)
        client=Client(root);clients.append(client);return client

    h=OpencodeHarness(Store(tmp_path),factory)
    def get_client():
        try:obtained.append(h._client())
        except Exception as exc:errors.append(exc)
    def close_harness():
        close_entered.set()
        try:h.close()
        except Exception as exc:errors.append(exc)

    starting=threading.Thread(target=get_client)
    closing=threading.Thread(target=close_harness)
    try:
        starting.start();assert started.wait(2)
        closing.start();assert close_entered.wait(2)
        assert not h._closed and not clients
        allow_start.set()
        starting.join(2);closing.join(2)
        assert not starting.is_alive() and not closing.is_alive()
        assert not errors and obtained==clients and len(clients)==1
        assert clients[0].closed and h.client is None
    finally:
        allow_start.set()
        if starting.ident:starting.join(2)
        if closing.ident:closing.join(2)
        h.close()
