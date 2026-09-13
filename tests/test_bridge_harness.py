import queue
import json
import time
import pytest
from briefloop.store import Store
from briefloop.bridge_harness import BridgeHarness, normalize_bridge_usage


class BridgeFixture:
    def __init__(self):self.starts=[];self.sinks={}
    def subscribe(self,eid):self.sinks[eid]=queue.Queue();return self.sinks[eid]
    def unsubscribe(self,eid):self.sinks.pop(eid,None)
    def call(self,method,params,timeout=None):
        if method=='start':
            self.starts.append(params)
            for event in [{'kind':'session','session_id':'native-session'},
                          {'kind':'reasoning','text':'weighing options'},
                          {'kind':'text','text':'visible answer'},
                          {'kind':'usage','usage':{'input_tokens':12,'output_tokens':5}},
                          {'kind':'tool','id':'native-item','name':'read','status':'completed','input':{'path':'sample.txt'},'output':'data'},
                          {'kind':'end','status':'completed'}]:self.sinks[params['execution_id']].put(event)
            return {'execution_id':params['execution_id']}
        return {}


@pytest.mark.parametrize('backend', ['claude', 'codebuddy'])
def test_bridge_turn_is_durable_and_same_message_is_not_redispatched(tmp_path, backend):
    bridge=BridgeFixture();h=BridgeHarness(Store(tmp_path),bridge,backend)
    s=h.create_session('test',{'model':'host-model'})
    message=h.send(s['id'],'read the fixture',message_id='fixed-admission')
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        snap=h.snapshot(s['id'])
        if snap['messages'][0]['status']=='completed':break
        time.sleep(.01)
    assert snap['messages'][0]['status']=='completed'
    assert snap['token_usage']['last']=={'inputTokens':12,'outputTokens':5,'cachedInputTokens':None}
    saved=h.store.rows("SELECT data FROM chat_events WHERE kind='thread/tokenUsage/updated'")
    assert json.loads(saved[-1]['data'])['tokenUsage']['last']['inputTokens']==12
    assert snap['session']['thread_id']=='native-session'
    assert snap['messages'][1]['text']=='visible answer'
    assert 'reasoning' not in snap['messages'][1]
    assert h.snapshot(s['id'],reasoning=True)['messages'][1]['reasoning']=='weighing options'
    assert bridge.starts[0]['execution_id']=='fixed-admission'
    assert bridge.starts[0]['allow_web'] is None
    h.send(s['id'],'read the fixture',message_id='fixed-admission')
    assert len(bridge.starts)==1
    assert any(e['kind']=='item/completed' and e['data']['item']['id']=='native-item' for e in snap['events'])


def test_bridge_internal_managed_run_does_not_get_native_web_tools(tmp_path):
    """Internal research runs frozen to tavily/duckduckgo must use the metered
    CLI; granting host-native search here would bypass search budget accounting."""
    def finished(h,mid):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if any(m['id']==mid and m['status']=='completed' for m in h.snapshot(sessions[mid])['messages']):return True
            time.sleep(.01)
        raise AssertionError('turn did not finish')
    bridge=BridgeFixture();h=BridgeHarness(Store(tmp_path),bridge,'claude')
    sessions={}
    original_send=h.send
    def tracked_send(session_id,*args,message_id=None,**kwargs):
        sessions[message_id]=session_id
        return original_send(session_id,*args,message_id=message_id,**kwargs)
    h.send=tracked_send
    managed=h.start_internal('研究任务正文',display_text='研究',allow_web=True,search_provider='duckduckgo',message_id='ddg-run')
    finished(h,'ddg-run')
    assert bridge.starts[0]['web_tools'] is False
    internal_native=h.start_internal('原生研究',allow_web=True,search_provider='native',message_id='native-run')
    finished(h,'native-run')
    assert bridge.starts[1]['web_tools'] is True
    chat=h.create_session('chat',{'model':'host-model'})
    sessions['chat-turn']=chat['id']
    h.send(chat['id'],'联网查一下',allow_web=True,message_id='chat-turn')
    finished(h,'chat-turn')
    assert bridge.starts[2]['web_tools'] is True


@pytest.mark.parametrize('backend', ['kimi', 'codebuddy'])
def test_restricted_reviewer_cannot_enter_unverified_bridge(tmp_path, backend):
    bridge=BridgeFixture();h=BridgeHarness(Store(tmp_path),bridge,backend)
    with pytest.raises(ValueError,match='隔离'):
        h.create_session('review',{'model':'default','permission':'read-only','review_root':str(tmp_path)})
    assert not bridge.starts


def test_bridge_host_gets_the_workspace_contract_once_per_native_session(tmp_path):
    bridge=BridgeFixture();h=BridgeHarness(Store(tmp_path),bridge,'claude')
    s=h.create_session('test',{'model':'host-model'})
    def turn(text,mid):
        h.send(s['id'],text,message_id=mid)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if any(m['id']==mid and m['status']=='completed' for m in h.snapshot(s['id'])['messages']):return
            time.sleep(.01)
        raise AssertionError('turn did not finish')
    turn('你是谁','m1')
    # The host CLI answers as its own product unless BriefLoop frames the request.
    assert bridge.starts[0]['prompt'].startswith('你是此本地 BriefLoop 工作区的交互助手')
    assert bridge.starts[0]['prompt'].rstrip().endswith('你是谁\n本轮不主动检索网络来源，仅使用已提供材料。')
    assert '不要原文复述' in bridge.starts[0]['prompt']
    # A resumed native session already holds the contract; do not pay for it again.
    turn('继续','m2')
    assert 'BriefLoop 工作区的交互助手' not in bridge.starts[1]['prompt']
    assert bridge.starts[1]['prompt'].startswith('继续')
    # A changed contract (here: the search provider) must be re-sent to that session.
    h.store.set_meta('settings',{**h.store.settings(),'search_provider':'tavily'})
    turn('再继续','m3')
    assert bridge.starts[2]['prompt'].startswith('你是此本地 BriefLoop 工作区的交互助手')
    assert 'Tavily' in bridge.starts[2]['prompt']


def test_codebuddy_saved_usage_is_reprojected_without_rewriting_history(tmp_path):
    store=Store(tmp_path);h=BridgeHarness(store,BridgeFixture(),'codebuddy')
    session=h.create_session('Synthetic saved session',{'model':'deepseek-v4.1-flash'})
    raw={'sessionUpdate':'usage_update','used':30000,'size':1000000,
         '_meta':{'usage':{'prompt_tokens':29217,'completion_tokens':571,'total_tokens':29788,
                          'cache_read_input_tokens':0,'cached_tokens':0,
                          'prompt_tokens_details':{'cached_tokens':29056},'prompt_cache_hit_tokens':29056}}}
    h.chat.event(session['id'],'thread/tokenUsage/updated',
                 {'tokenUsage':{'backend':'codebuddy','last':{'inputTokens':None,'outputTokens':None},'raw':raw}})
    before=store.rows('SELECT * FROM chat_events')
    snapshot=h.snapshot(session['id'])
    usage=snapshot['token_usage']
    assert usage['last']=={'inputTokens':29217,'cachedInputTokens':29056,'outputTokens':571}
    assert usage['contextUsedTokens']==30000 and usage['modelContextWindow']==1000000
    assert snapshot['events'][-1]['data']['tokenUsage']==usage
    assert h.snapshot(session['id'],after=snapshot['events'][-1]['seq'])['token_usage']==usage
    assert store.rows('SELECT * FROM chat_events')==before
    h.chat.event(session['id'],'thread/providerChanged',{})
    assert h.snapshot(session['id'])['token_usage'] is None


def test_usage_unknown_and_zero_do_not_become_estimates():
    usage=normalize_bridge_usage({'sessionUpdate':'usage_update','used':123,'size':1000},'codebuddy')
    assert usage['last']=={'inputTokens':None,'outputTokens':None,'cachedInputTokens':None}
    assert usage['contextUsedTokens']==123 and usage['modelContextWindow']==1000
    invalid=normalize_bridge_usage({'input_tokens':True,'output_tokens':-1,'cache_read_input_tokens':'8',
                                    'sessionUpdate':'usage_update','used':float('nan'),'size':0},'codebuddy')
    assert all(value is None for value in invalid['last'].values())
    assert invalid['contextUsedTokens'] is None and invalid['modelContextWindow'] is None
    zero=normalize_bridge_usage({'input_tokens':0,'output_tokens':0,'cache_read_input_tokens':0,
                                 'used':42,'size':99},'unknown-host')
    assert zero['last']=={'inputTokens':0,'outputTokens':0,'cachedInputTokens':0}
    assert zero['contextUsedTokens'] is None and zero['modelContextWindow'] is None


def test_codebuddy_sparse_usage_reuses_only_same_request_message_without_rewriting_history(tmp_path):
    store=Store(tmp_path);h=BridgeHarness(store,BridgeFixture(),'codebuddy')
    sid=h.create_session('Sparse native usage',{'model':'deepseek-v4.1-flash'})['id']
    identity={'codebuddy.ai/requestId':'request-1','codebuddy.ai/messageId':'message-1'}
    complete={'sessionUpdate':'usage_update','used':33947,'size':1000000,
              '_meta':{**identity,'usage':{'prompt_tokens':33947,'completion_tokens':102,
                                        'prompt_cache_hit_tokens':23296}}}
    sparse={'sessionUpdate':'usage_update','used':33947,'size':1000000,
            '_meta':{**identity,'codebuddy.ai/usageByCategory':{'conversation':8645,'tools':23258}}}
    for raw in (complete,sparse):
        h.chat.event(sid,'thread/tokenUsage/updated',{'tokenUsage':normalize_bridge_usage(raw,'codebuddy')})
    before=store.rows('SELECT * FROM chat_events')
    snapshot=h.snapshot(sid)
    expected={'inputTokens':33947,'cachedInputTokens':23296,'outputTokens':102}
    assert snapshot['token_usage']['last']==expected
    assert snapshot['events'][-1]['data']['tokenUsage']['last']==expected
    assert snapshot['token_usage']['raw']==sparse
    after=snapshot['events'][-1]['seq']
    assert h.snapshot(sid,after=after)['token_usage']['last']==expected
    assert h.snapshot(sid,after=after-1)['events'][0]['data']['tokenUsage']['last']==expected
    assert store.rows('SELECT * FROM chat_events')==before

    # An explicitly reported zero is not a missing value.
    zero={'_meta':{**identity,'usage':{'prompt_tokens':0,'completion_tokens':0,'prompt_cache_hit_tokens':0}}}
    h.chat.event(sid,'thread/tokenUsage/updated',{'tokenUsage':normalize_bridge_usage(zero,'codebuddy')})
    usage=h.snapshot(sid)['token_usage']
    assert usage['last']=={'inputTokens':0,'cachedInputTokens':0,'outputTokens':0}
    assert usage['modelContextWindow']==1000000
    h.chat.event(sid,'thread/providerChanged',{})
    h.chat.event(sid,'thread/tokenUsage/updated',{'tokenUsage':normalize_bridge_usage(sparse,'codebuddy')})
    assert all(v is None for v in h.snapshot(sid)['token_usage']['last'].values())


@pytest.mark.parametrize('change', ['request','message','missing_identity'])
def test_codebuddy_sparse_usage_never_inherits_other_or_unknown_request(tmp_path,change):
    store=Store(tmp_path);h=BridgeHarness(store,BridgeFixture(),'codebuddy')
    sid=h.create_session('Usage request boundary',{'model':'default'})['id']
    meta={'codebuddy.ai/requestId':'request-1','codebuddy.ai/messageId':'message-1'}
    full={'_meta':{**meta,'usage':{'prompt_tokens':33947,'completion_tokens':102,'prompt_cache_hit_tokens':23296}}}
    h.chat.event(sid,'thread/tokenUsage/updated',{'tokenUsage':normalize_bridge_usage(full,'codebuddy')})
    if change=='request':meta['codebuddy.ai/requestId']='request-2'
    elif change=='message':meta['codebuddy.ai/messageId']='message-2'
    else:meta.pop('codebuddy.ai/messageId')
    h.chat.event(sid,'thread/tokenUsage/updated',{'tokenUsage':normalize_bridge_usage({'_meta':meta},'codebuddy')})
    assert all(v is None for v in h.snapshot(sid)['token_usage']['last'].values())
