"""No model calls: preserve publication and durable conversational recovery."""
import json
from types import SimpleNamespace
import pytest
from briefloop.interactive_runtime import InteractiveRuntime
from briefloop.store import Store


class FakeHarness:
    def __init__(self):
        self.sessions = {}; self.starts = []; self.cancelled = []
        self.client = SimpleNamespace(process=SimpleNamespace(pid=123, poll=lambda: None))

    def create_session(self, title, runtime, cwd):
        sid = 'session-' + str(len(self.sessions))
        self.sessions[sid] = {'session': {'id': sid, 'status': 'idle', 'lifecycle':'active'}, 'messages': [], 'events': []}
        return self.sessions[sid]['session']

    def start_internal(self, text, **kwargs):
        sid = kwargs['session_id']; mid = kwargs['message_id']
        if not any(m['id'] == mid for m in self.sessions[sid]['messages']):
            self.starts.append((text, kwargs))
            self.sessions[sid]['messages'].append({'id': mid, 'role': 'user', 'text': kwargs['display_text'],
                                                  'status': 'delivered', 'turn_id': mid})
        return SimpleNamespace(session_id=sid, message_id=mid)

    def snapshot(self, sid, after=0):
        snap = self.sessions[sid]
        return {**snap, 'events': [e for e in snap['events'] if e['seq'] > after]}

    def finish(self, sid, status='completed'):
        snap = self.sessions[sid]; message = snap['messages'][-1]
        message['status'] = status
        snap['messages'].append({'id': 'reply-' + message['id'], 'role': 'assistant', 'text': '已保存稿件',
                                 'status': 'completed', 'turn_id': message['turn_id']})
        seq = len(snap['events']) + 1
        snap['events'].extend([
            {'seq': seq, 'kind': 'thread/tokenUsage/updated', 'data': {'tokenUsage': {'total': {'totalTokens': 10}}}},
            {'seq': seq+1, 'kind': 'turn/completed', 'data': {'status': status, 'turnId': message['turn_id']}}])

    def cancel(self, sid):
        self.cancelled.append(sid)
        for message in self.sessions[sid]['messages']:
            if message['status'] == 'delivered': message['status'] = 'interrupted'


def setup(tmp_path):
    store = Store(tmp_path / 'workspace')
    job = store.enqueue('generate', {})
    job['allow_web'] = True
    harness = FakeHarness()
    return store, job, harness, InteractiveRuntime(store, harness), store.root / 'jobs' / job['id']


def legacy_review_case(tmp_path):
    from briefloop.review import enqueue_review,run_review
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'agent_backend':'opencode','model':'example/vision','auto_learn':False})
    source=store.add_source('Synthetic','Revenue was 12.')
    run=store.create_run({'title':'Report','objective':'Read revenue'},[source['id']])
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue was 12.'})
    job=enqueue_review(store,brief['id']);folder=store.root/'jobs'/job['id']
    class ReviewHarness(FakeHarness):
        def start_internal(self,text,**kwargs):
            super().start_internal(text,**kwargs)
            snapshot=self.sessions[kwargs['session_id']];message=snapshot['messages'][-1]
            if len(self.starts)==1:message['status']='failed';return
            index=json.loads((folder/'packet/index.json').read_text())
            reply={'version_id':brief['id'],'fingerprint':index['fingerprint'],'status':'incomplete',
                   'summary':'Transport fixture only; no semantic review.',
                   'unchecked_items':[{'description':'No semantic review in this test','importance':'core'}],
                   'assessment':{'brief_hash':brief['hash'],'status':'incomplete','summary':'Not reviewed','overall':'建议修改'}}
            message['status']='completed'
            snapshot['messages'].append({'id':'reply-'+message['id'],'role':'assistant','text':json.dumps(reply),
                                         'status':'completed','turn_id':message['turn_id']})
    class LegacyRuntime(InteractiveRuntime):
        def execute(self,job,*args,**kwargs):
            # The pre-attachment runtime persisted this exact binding shape.
            return super().execute({k:v for k,v in job.items() if k!='review_id'},*args,**kwargs)
    harness=ReviewHarness();legacy=LegacyRuntime(store,backends={'opencode':harness})
    with pytest.raises(RuntimeError,match='Agent 执行失败'):run_review(store,legacy,job,brief['id'],folder)
    store.update_job(job['id'],'failed',error='Synthetic transport failure')
    return store,job,brief,folder,harness


def test_legacy_review_binding_adds_only_validated_identity_and_reuses_result(tmp_path):
    from briefloop.review import run_review
    from briefloop.runtime import Worker
    store,job,brief,folder,harness=legacy_review_case(tmp_path)
    marker=folder/'conversation.json';before=json.loads(marker.read_text())
    assert 'review_id' not in before['runtime']
    runtime=InteractiveRuntime(store,backends={'opencode':harness})
    resumed=Worker(store,runtime).resume(job['id'])
    result=run_review(store,runtime,resumed,brief['id'],folder)
    after=json.loads(marker.read_text())
    assert resumed['id']==job['id'] and after['session_id']==before['session_id']
    assert after['history']==[before['message_id']] and len(harness.starts)==2
    assert after['runtime']=={**before['runtime'],'review_id':result['id']}
    assert harness.starts[-1][1]['runtime']['permission']=='read-only'
    assert harness.starts[-1][1]['runtime']['review_root']==before['runtime']['review_root']
    assert run_review(store,runtime,resumed,brief['id'],folder)['id']==result['id']
    assert len(harness.starts)==2  # An admitted saved result is not resampled.


@pytest.mark.parametrize('changed',['job_id','backend','model','permission','packet_root','packet_bytes','review_owner'])
def test_legacy_review_binding_rejects_changed_execution_or_packet(tmp_path,changed):
    from briefloop.runtime import stage_job
    store,job,brief,folder,harness=legacy_review_case(tmp_path)
    marker=folder/'conversation.json';binding=json.loads(marker.read_text())
    review_id=json.loads((folder/'review-id.json').read_text())['review_id']
    if changed=='job_id':binding['job_id']='another-job'
    elif changed=='backend':binding['backend']='codex'
    elif changed=='model':binding['runtime']['model']='example/other-model'
    elif changed=='permission':binding['runtime']['permission']='workspace-write'
    elif changed=='packet_root':binding['runtime']['review_root']=str(store.root/'other-packet')
    elif changed=='packet_bytes':
        target=folder/'packet/target.json';target.write_bytes(target.read_bytes()+b' ')
    else:
        other=store.enqueue('review',{'version_id':brief['id']})
        with store.tx() as connection:connection.execute('UPDATE reviews SET job_id=? WHERE id=?',(other['id'],review_id))
    marker.write_text(json.dumps(binding));original=marker.read_bytes()
    stage=stage_job(store,job,'evaluator',mode='single')
    stage.update(readonly_output='review.json',review_id=review_id,input_source_ids=[])
    runtime=InteractiveRuntime(store,backends={'opencode':harness})
    with pytest.raises(ValueError):runtime.execute(stage,'Resume original review',folder)
    assert len(harness.starts)==1 and marker.read_bytes()==original


def test_failed_provider_connection_reaches_job_error_as_fixed_public_text(tmp_path):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'model':'synthetic-model'})
    job=store.enqueue('generate',{})
    folder=store.root/'jobs'/job['id']
    class FailedHarness(FakeHarness):
        def start_internal(self,text,**kwargs):
            super().start_internal(text,**kwargs)
            sid=kwargs['session_id'];self.finish(sid,'failed')
            snap=self.sessions[sid]
            snap['events'].append({'seq':3,'kind':'error','data':{'message':'Connection error. SECRET https://private.invalid'}})
    runtime=InteractiveRuntime(store,FailedHarness())
    with pytest.raises(RuntimeError,match='模型连接失败') as exc:
        runtime.execute(job,'synthetic',folder)
    assert 'SECRET' not in str(exc.value)
    store.update_job(job['id'],'failed',error=str(exc.value))
    from briefloop.task_progress import summary
    result=summary(store,job['id'])
    assert '模型连接失败' in result['error']
    assert 'SECRET' not in json.dumps(result)
