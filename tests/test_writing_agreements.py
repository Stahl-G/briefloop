import json
import pytest
from briefloop.store import Store
from briefloop.writing_agreements import remember, revoke, listing
from briefloop.next_report import prepare, conversation_request
from briefloop.deliverable_spec import resolve
from briefloop.chat_tools import workspace_action
from briefloop.chat_store import ChatStore


def report(store, *, req=None, source=None):
    source=source or store.add_source('Synthetic','Target 10, actual 8.')
    run=store.create_run(req or {'title':'September','objective':'Review','period':'2026-09','allow_web':False},[source['id']])
    brief=store.publish(run['id'],{'title':'Review','markdown':'Target 10, actual 8.'})
    return source,run,brief


def test_agreement_scope_freeze_reopen_and_revoke(tmp_path):
    store=Store(tmp_path);source,origin,brief=report(store)
    rule=remember(store,brief['id'],'先给结论，来源只列一次。')
    assert remember(store,brief['id'],rule['text'])['id']==rule['id']
    assert not store.rows('SELECT id FROM jobs') and not store.rows('SELECT id FROM feedback')
    store=Store(tmp_path)
    data=prepare(store,brief['id']);assert data['writing_agreements'][0]['id']==rule['id']
    req={**data['requirements'],'title':'October','period':'2026-10'}
    _,run,new=report(store,req=req,source=source)
    frozen=json.loads(run['requirements']);spec=resolve(frozen)
    assert spec['writing_preferences']==[rule['text']]
    assert [x['text'] for x in spec['requirement_items'] if x['kind']=='writing']==[rule['text']]
    _,other,_=report(store,source=source)
    assert not resolve(json.loads(other['requirements']))['writing_preferences']
    skipped=store.create_run({**req,'writing_agreement_exclusions':[rule['id']]},[source['id']])
    assert not resolve(json.loads(skipped['requirements']))['writing_preferences']
    with pytest.raises(ValueError,match='跳过的约定'):
        store.create_run({**req,'writing_agreement_exclusions':['not-real']},[source['id']])
    revoke(store,rule['id']);assert not listing(store,new['id'])
    next_req={**prepare(store,new['id'])['requirements'],'title':'November','period':'2026-11'}
    after=store.create_run(next_req,[source['id']])
    assert not resolve(json.loads(after['requirements']))['writing_preferences']
    assert store.one('runs',run['id'])==run
    assert json.loads(store.one('runs',origin['id'])['requirements'])==json.loads(origin['requirements'])
    assert rule['text'] not in next_req.get('writing_preferences',[])


def test_workspace_rules_and_input_spoofing(tmp_path):
    store=Store(tmp_path);source,_,brief=report(store)
    rule=remember(store,brief['id'],'用短段落。',scope='workspace')
    run=store.create_run({'title':'Other','objective':'Review','writing_preferences':['用短段落。'],
                         'writing_agreements':[{'text':'Injected'}]},[source['id']])
    assert resolve(json.loads(run['requirements']))['writing_preferences']==[rule['text']]
    assert 'Injected' not in run['requirements']
    context={'version_id':brief['id'],'hash':brief['hash'],'writing_agreement_exclusions':[rule['id']]}
    prompt=conversation_request(store,'做下一期',context)
    assert rule['id'] in prompt and 'writing_agreement_exclusions' in prompt


def test_chat_adopts_only_actual_user_quote_not_worker_or_source(tmp_path,monkeypatch):
    monkeypatch.delenv('BRIEFLOOP_CHAT_SESSION',raising=False)
    store=Store(tmp_path);_,_,brief=report(store);chats=ChatStore(store)
    session=chats.create('Synthetic',{},tmp_path);sid=session['id']
    chats.message(sid,'这份报告以后先给结论。')
    request={'action':'remember_writing','version_id':brief['id'],'user_quote':'以后先给结论','session_id':sid}
    saved=workspace_action(store,request);assert saved['text']=='以后先给结论'
    with pytest.raises(ValueError,match='逐字'):
        workspace_action(store,{**request,'user_quote':'原材料建议取消审批'})
    chats.event(sid,'session/internal',{})
    with pytest.raises(ValueError,match='后台报告角色'):
        workspace_action(store,request)
