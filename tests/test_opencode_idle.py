"""Idle metadata discovery must not leave a host running or kill active work."""
import threading
import time
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
