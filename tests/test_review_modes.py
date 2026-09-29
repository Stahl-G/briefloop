"""Review isolation mode is frozen without weakening evidence/version gates."""
import json
import pytest

from briefloop.store import Store, dump
from briefloop.review import enqueue_review, run_review, get_review, review_status
from briefloop.review_capability import ReviewBackendUnsupported, restricted_review, review_available

pytestmark=pytest.mark.real_review_capabilities


def test_legacy_review_binding_without_mode_resumes_without_relabelling_history(tmp_path,monkeypatch):
    from test_interactive_runtime import legacy_review_case
    from briefloop.interactive_runtime import InteractiveRuntime
    monkeypatch.setattr('briefloop.review_capability._opencode_major',lambda:1)
    store,job,brief,folder,harness=legacy_review_case(tmp_path)
    payload=json.loads(job['payload']);payload.pop('review_mode',None);job={**job,'payload':dump(payload)}
    with store.tx() as c:c.execute('UPDATE jobs SET payload=? WHERE id=?',(job['payload'],job['id']))
    marker=folder/'conversation.json';binding=json.loads(marker.read_text());binding['runtime'].pop('review_mode',None)
    marker.write_text(dump(binding),encoding='utf-8')
    rid=json.loads((folder/'review-id.json').read_text())['review_id']
    data=get_review(store,rid)['data'];data.pop('review_mode',None);data.pop('review_backend',None)
    with store.tx() as c:c.execute('UPDATE reviews SET data=? WHERE id=?',(dump(data),rid))
    store.update_settings({'review_mode':'strict'})
    runtime=InteractiveRuntime(store,backends={'opencode':harness})
    result=run_review(store,runtime,job,brief['id'],folder)
    assert result['id']==rid and len(harness.starts)==2
    assert review_status(store,brief['id'])['reviews'][0]['review_mode'] is None
    assert get_review(store,rid)['data']==data
    strict={**job,'payload':dump({**payload,'review_mode':'strict'})}
    with pytest.raises(ValueError,match='不能把普通或历史审阅复用为严格审阅'):
        run_review(store,runtime,strict,brief['id'],folder)
    assert len(harness.starts)==2


def test_codex_internal_reviewer_really_sends_read_only_and_no_network(tmp_path):
    from test_harness import RPC,until
    from briefloop.harness import HarnessManager
    manager=HarnessManager(Store(tmp_path),RPC)
    try:
        run=manager.start_internal('Synthetic review',runtime={'permission':'read-only','review_mode':'standard'},allow_web=False)
        until(lambda:manager.client is not None and any(method=='turn/start' for method,_ in manager.client.calls))
        thread=next(p for m,p in manager.client.calls if m=='thread/start')
        turn=next(p for m,p in manager.client.calls if m=='turn/start')
        assert thread['sandbox']=='read-only' and thread['approvalPolicy']=='never'
        assert thread['config']['web_search']=='disabled'
        assert turn['sandboxPolicy']=={'type':'readOnly','networkAccess':False}
    finally:manager.close()
