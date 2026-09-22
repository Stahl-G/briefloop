"""Explicit human intent survives scoring and bounded candidate repair."""
import json
from pathlib import Path
import pytest
from wikiskill import feedback_loop,native_agents
from briefloop.store import Store,content_hash,dump,now
from briefloop.learning import _experience,_sync_wiki,apply_accepted
from briefloop.skills import bind_context
from briefloop.models import Comment


def candidate(root,text='# Method\nUse owner/action/date.'):
    h=native_agents.dispatch(root,'codex')['handoffs'][0]
    native_agents.bind(root,h['request_id'],'synthetic-maintainer-'+h['request_id'],'codex','fresh')
    fid=feedback_loop.work(root)['feedback'][-1]['id']
    Path(h['result_file']).write_text(json.dumps({'patterns':[{'name':'Actions','content':'Use owner/action/date.','sources':[fid]}]}))
    native_agents.collect(root,h['request_id'])
    h=native_agents.dispatch(root,'codex')['handoffs'][0]
    native_agents.bind(root,h['request_id'],'synthetic-proposer-'+h['request_id'],'codex','fresh')
    skill=Path(h['output_directory'])/'SKILL.md';skill.write_text(text)
    Path(h['result_file']).write_text(json.dumps({'skill':str(skill),'note':'Synthetic candidate'}))
    native_agents.collect(root,h['request_id'])


def pair(verdict='tie',regressions=None,fulfilled=True):
    return {'case_id':'case','verdict':verdict,'regressions':regressions or [],
            'requirement_checks':[{'source':'f1','fulfilled':fulfilled,'evidence':'Candidate action table includes owner/action/date.'}]}


def start(root,explicit=True):
    return feedback_loop.begin(root,rounds=2,feedback=[{'text':json.dumps({'comment':'Use owner/action/date'}),'source':'f1','origin':'human','learning_intent':'explicit_requirement' if explicit else 'feedback'}])


def test_explicit_tie_accepted_but_ordinary_tie_not(tmp_path):
    for explicit in (True,False):
        root=tmp_path/str(explicit);start(root,explicit);candidate(root)
        result=feedback_loop.finish(root,pairs=[pair()])
        assert result['history'][-1]['accepted'] is explicit


def test_regression_repairs_then_accepts_tie_preserving_history(tmp_path):
    root=tmp_path/'study';start(root);candidate(root)
    state=feedback_loop.finish(root,pairs=[pair('worse',['Unsupported figure'])])
    assert state['phase']=='maintainer' and state['round']==2
    assert state['history'][0]['verdict']=='REVISION_REQUIRED'
    candidate(root,'# Revised method\nUse owner/action/date; unknowns remain unknown.')
    state=feedback_loop.finish(root,pairs=[pair()])
    assert state['phase']=='complete' and state['history'][1]['accepted']
    assert not state['history'][0]['accepted'] and state['feedback'][0]['learning_intent']=='explicit_requirement'


def test_exhaustion_retains_pending_requirement_in_visible_wiki(tmp_path):
    store=Store(tmp_path/'workspace');root=tmp_path/'study';start(root)
    for _ in range(2):
        candidate(root);state=feedback_loop.finish(root,pairs=[pair('worse')])
    assert state['phase']=='complete' and state['history'][-1]['requirements_pending']
    assert state['current_skill'] is None
    store.set_meta('last_study',str(root));_sync_wiki(store,root)
    text=(store.root/'wiki/index.md').read_text()
    assert 'Use owner/action/date' in text and '待完善' in text and '人类明确要求' in text


def test_missing_checks_cannot_claim_explicit_adoption(tmp_path):
    root=tmp_path/'study';start(root);candidate(root)
    value=pair();value.pop('requirement_checks')
    with pytest.raises(ValueError,match='evidence-backed'):feedback_loop.finish(root,pairs=[value])
    assert feedback_loop.work(root)['history']==[]


def test_entry_provenance_is_explicit_not_guessed(tmp_path):
    store=Store(tmp_path);s=store.add_source('Synthetic','Three files.')
    run=store.create_run({'title':'Synthetic','objective':'Summarize'},[s['id']])
    b=store.publish(run['id'],{'title':'Synthetic','markdown':'Three files.'})
    ordinary=store.comment(b['id'],'Must use a table')
    explicit=store.comment(b['id'],'Use owner/action/date',learning_intent='explicit_requirement')
    job={'payload':json.dumps({'feedback_ids':[ordinary['id'],explicit['id']]})}
    feedback,_=_experience(store,job)
    assert [x['learning_intent'] for x in feedback]==['feedback','explicit_requirement']
    assert all(x['origin']=='human' for x in feedback)
    assert Comment(version_id=b['id'],text='ordinary').learning_intent=='feedback'


def test_inherited_requirement_does_not_promote_automatic_tie(tmp_path):
    first=tmp_path/'first';start(first);candidate(first)
    feedback_loop.finish(first,pairs=[pair()])
    second=tmp_path/'second'
    feedback_loop.begin(second,previous=first,feedback=[{'text':'Automatic observation','source':'auto1','origin':'automatic'}])
    candidate(second)
    result=feedback_loop.finish(second,pairs=[pair()])
    assert not result['history'][-1]['accepted']
    assert result['history'][-1]['policy']=='lightweight_pairwise'
    assert any(x.get('learning_intent')=='explicit_requirement' for x in result['feedback'])


def test_model_retry_retains_explicit_scope_without_duplicate_feedback(tmp_path):
    first=tmp_path/'first';start(first)
    second=tmp_path/'second'
    state=feedback_loop.begin(second,previous=first,feedback=[],rounds=2,requirement_sources=['f1'])
    assert len(state['feedback'])==1 and state['explicit_requirement_sources']==['f1']
    candidate(second)
    assert feedback_loop.finish(second,pairs=[pair()])['history'][-1]['accepted']


def test_accepted_same_text_binds_targets_without_rewriting_old_versions(tmp_path):
    store=Store(tmp_path/'workspace')
    text='# Method\nUse owner/action/date.'
    legacy_id='skill_'+content_hash(text)[:16]
    with store.tx() as connection:
        connection.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)',
                           (legacy_id,None,text,dump(['scout']),'Legacy binding',now()))
    store.bind_skill(legacy_id)
    legacy=store.one('skills',legacy_id)
    source=store.add_source('Historical facts','Three files.')
    historical=store.create_run({'title':'Historical','objective':'Summarize'},[source['id']])
    initial=tmp_path/'initial.md';initial.write_text(text,encoding='utf-8')

    def accept(name,targets):
        study=tmp_path/name
        feedback_loop.begin(study,skill=initial,feedback=[{'text':'Use owner/action/date',
            'source':'f1','origin':'human','learning_intent':'explicit_requirement'}])
        candidate(study,text)
        state=feedback_loop.finish(study,pairs=[pair()])
        assert state['history'][-1]['accepted']
        job=store.enqueue('learn',{'skill_id':store.meta('active_skill'),'targets':targets})
        apply_accepted(store,job,study,state)
        return store.one('skills',store.meta('active_skill')),job,study,state

    analyst,_,_,_=accept('analyst',['analyst'])
    both,_,_,_=accept('both',['scout','analyst'])
    reordered,job,study,state=accept('reordered',['analyst','scout','analyst'])
    assert analyst['id']!=both['id'] and reordered['id']==both['id']
    assert set(bind_context(store,analyst))=={'analyst'}
    assert set(bind_context(store,both))=={'scout','analyst'}
    assert json.loads(both['targets'])==['analyst','scout']
    assert store.one('skills',legacy_id)==legacy
    assert store.one('runs',historical['id'])['skill_id']==legacy_id
    assert set(bind_context(store,legacy))=={'scout'}
    assert len(store.rows('SELECT id FROM skills'))==3
    # A completed adoption replay must still preserve a later user rollback.
    store.bind_skill(legacy_id)
    apply_accepted(store,job,study,state)
    assert store.meta('active_skill')==legacy_id
