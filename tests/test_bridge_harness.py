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
