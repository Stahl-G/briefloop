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



def test_untrusted_chat_cannot_adopt_or_revoke_and_missing_report_is_readable(tmp_path,monkeypatch):
    store=Store(tmp_path);_,_,brief=report(store);chats=ChatStore(store)
    sid=chats.create('Synthetic',{},tmp_path)['id']
    chats.message(sid,'以后先给结论')
    # Neither a copied public session ID nor a caller-supplied environment establishes authority.
    monkeypatch.setenv('BRIEFLOOP_CHAT_SESSION',sid)
    for action in ('remember_writing','forget_writing'):
        with pytest.raises(ValueError,match='报告页'):
            workspace_action(store,{'action':action,'session_id':sid,'version_id':brief['id'],'user_quote':'以后先给结论'})
    assert not listing(store,brief['id'])
    for read in (listing,prepare):
        with pytest.raises(ValueError,match='报告版本不存在'):read(store,'missing')


def test_next_period_selection_is_bound_to_executing_message_not_model_copy(tmp_path):
    from briefloop.next_report import bind_message_context
    store=Store(tmp_path);source,_,brief=report(store);chats=ChatStore(store)
    store.update_settings({'model':'synthetic-no-call'})
    sid=chats.create('Next report',{},tmp_path)['id']
    rule=remember(store,brief['id'],'先给结论')
    context={'version_id':brief['id'],'hash':brief['hash'],'writing_agreement_exclusions':[rule['id']]}
    bind_message_context(store,sid,'first',context)
    chats.message(sid,'做十月一期',mid='first',status='delivered')
    # The model must name the origin, but need not copy the UI exclusions.
    request={'action':'generate','session_id':sid,'requirements':{'title':'October','objective':'Review','period':'2026-10','previous_report_version_id':brief['id'],'previous_report_hash':brief['hash']},'source_ids':[source['id']]}
    bind_message_context(store,sid,'queued',None)
    chats.message(sid,'别的要求',mid='queued')
    before=(len(store.rows('SELECT id FROM runs')),len(store.rows('SELECT id FROM jobs')))
    for override in ({'previous_report_version_id':None,'previous_report_hash':None},
                     {'previous_report_version_id':'another-version'},{'previous_report_hash':'different'}):
        with pytest.raises(ValueError,match='取消关联'):
            workspace_action(store,{**request,'requirements':{**request['requirements'],**override}})
        assert before==(len(store.rows('SELECT id FROM runs')),len(store.rows('SELECT id FROM jobs')))
    result=workspace_action(store,request)
    frozen=json.loads(store.one('runs',result['run_id'])['requirements'])
    assert frozen['previous_report_version_id']==brief['id']
    assert frozen['writing_agreement_exclusions']==[rule['id']]
    assert not resolve(frozen)['writing_preferences']
    with pytest.raises(ValueError,match='session_id'):
        workspace_action(store,{k:v for k,v in request.items() if k!='session_id'})
    # Unrelated terminal / another chat must not be blocked by the bound conversation.
    unrelated={**request,'requirements':{'title':'Other','objective':'Other purpose'}}
    for owner in (None,chats.create('Unrelated',{},tmp_path)['id']):
        unbound=workspace_action(store,{**unrelated,'session_id':owner})
        saved=json.loads(store.one('runs',unbound['run_id'])['requirements'])
        assert 'previous_report_version_id' not in saved and not resolve(saved)['writing_preferences']
    # A retry cannot replace the browser's original choice.
    with pytest.raises(ValueError,match='同一消息'):
        bind_message_context(store,sid,'first',{**context,'writing_agreement_exclusions':[]})
    # Only the next delivered message clears its own context; a queued one never changes an active turn.
    chats.patch_message('first',status='completed');chats.patch_message('queued',status='delivered')
    other=workspace_action(store,unrelated)
    assert 'previous_report_version_id' not in json.loads(store.one('runs',other['run_id'])['requirements'])


def test_followup_uses_small_reference_with_read_only_lookup(tmp_path):
    from briefloop.next_report import bind_message_context,context_already_delivered
    store=Store(tmp_path);_,_,brief=report(store);chats=ChatStore(store)
    sid=chats.create('Next',{},tmp_path)['id']
    context={'version_id':brief['id'],'hash':brief['hash']}
    bind_message_context(store,sid,'first',context)
    assert not context_already_delivered(store,sid,'second',context)  # Failed send: no message.
    chats.message(sid,'next',mid='first')
    assert not context_already_delivered(store,sid,'second',context)  # Merely queued.
    chats.patch_message('first',status='delivered')
    assert context_already_delivered(store,sid,'second',context)
    assert not context_already_delivered(store,sid,'second',{**context,'writing_agreement_exclusions':['different']})
    full=conversation_request(store,'确认十月',context)
    compact=conversation_request(store,'确认十月',context,compact=True)
    assert len(compact)<len(full) and '"objective"' not in compact and 'next_report' in compact
    assert brief['id'] in compact and brief['hash'] in compact
    lookup=workspace_action(store,{'action':'next_report','version_id':brief['id']})
    assert lookup['requirements']['objective']=='Review' and not store.rows('SELECT id FROM jobs')
    assert not context_already_delivered(store,sid,'first',context)  # Idempotent retry.
    chats.patch_message('first',status='failed')
    assert not context_already_delivered(store,sid,'second',context)
