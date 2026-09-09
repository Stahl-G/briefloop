import json
from pathlib import Path
import pytest
from briefloop.store import Store, Conflict
from briefloop.runtime import Worker
from briefloop.interactive_runtime import InteractiveRuntime, _usable_output


class ArtifactHarness:
    def __init__(self, produce):self.starts=0;self.sessions={};self.produce=produce
    def create_session(self,title,runtime,cwd):
        sid='s'+str(len(self.sessions));self.sessions[sid]={'session':{'id':sid,'status':'idle','lifecycle':'active'},'messages':[],'events':[]};return self.sessions[sid]['session']
    def start_internal(self,text,**kw):
        self.starts+=1;self.produce(Path(kw['cwd']))
        self.sessions[kw['session_id']]['messages'].append({'id':kw['message_id'],'role':'user','text':kw['display_text'],'status':'completed','turn_id':'t'+str(self.starts)})
    def snapshot(self,sid,after=0):return self.sessions[sid]
    def cancel(self,sid):pass


@pytest.mark.parametrize('generation',[False,True])
def test_rejected_hash_gets_new_turn_and_valid_assessment_is_reusable(tmp_path,generation):
    s=Store(tmp_path);src=s.add_source('Source','Revenue 12')
    run=s.create_run({'title':'Test','objective':'Summarize'},[src['id']])
    if generation:job=s.enqueue('generate',{'run_id':run['id']});vid='brief_'+job['id'][4:]
    else:
        brief=s.publish(run['id'],{'title':'Test','markdown':'Revenue 12'})
        vid=brief['id'];job=s.enqueue('assess',{'version_id':vid})
    scores=[]
    def produce(folder):
        if generation and folder.name!='evaluation':
            (folder/'draft.json').write_text(json.dumps({'title':'Test','markdown':'Revenue 12'}));return
        scores.append(folder)
        value={'brief_hash':'wrong' if len(scores)==1 else s.one('briefs',vid)['hash'],
               'summary':'Checked','overall':'达到要求','evidence':4,'coverage':4,'analysis':4,'expression':4}
        (folder/'assessment.json').write_text(json.dumps(value))
    harness=ArtifactHarness(produce);worker=Worker(s,InteractiveRuntime(s,harness))
    call=worker.generate if generation else worker.assess
    with pytest.raises(Conflict):call(job)
    s.update_job(job['id'],'failed',error='binding rejected')
    assert not s.rows('SELECT * FROM assessments')
    resumed=worker.resume(job['id']);call(resumed)
    assert len(scores)==2 and len(s.rows('SELECT * FROM assessments'))==1
    assert harness.starts==(3 if generation else 2)
    stage={**job,'runtime_role':'evaluator','evaluation_mode':'single'}
    assert _usable_output(stage,scores[-1],s)
    # Same admission helper also rejects nonexistent finding sources.
    value=json.loads((scores[-1]/'assessment.json').read_text())
    value['findings']=[{'dimension':'evidence','severity':'major','description':'test','source_id':'src_missing','evidence':'test'}]
    from briefloop.models import Assessment
    Assessment.model_validate(value)  # Reject on admission, not malformed schema.
    (scores[-1]/'assessment.json').write_text(json.dumps(value))
    assert not _usable_output(stage,scores[-1],s)
