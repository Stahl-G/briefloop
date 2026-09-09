"""Frozen per-role selection reaches both transports and keeps scoring separate."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from briefloop.models import ROLE_NAMES, Settings
from briefloop.store import Store, dump
from briefloop.runtime import Worker, CodexRuntime, stage_job
from briefloop.interactive_runtime import InteractiveRuntime
from briefloop.learning import enqueue_feedback, _role


def test_role_models_freeze_and_generation_scores_in_its_own_stage(tmp_path):
    store=Store(tmp_path/'workspace')
    roles={role:{'model':'test-'+role,'reasoning_effort':'medium'} for role in ROLE_NAMES}
    store.set_meta('settings',{**store.settings(),'role_models':roles})
    source=store.add_source('sample','预计交付，尚未完成。')
    run=store.create_run({'title':'交付','objective':'保留时间限定'},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id']})
    frozen=json.loads(job['payload'])
    store.set_meta('settings',{**store.settings(),'role_models':{}})
    assert json.loads(store.one('jobs',job['id'])['payload'])['role_models']==roles
    assert Settings().role_models=={}
    for role in ROLE_NAMES:
        assert json.loads(stage_job(store,job,role)['payload'])['runtime']==roles[role]
    legacy={**job,'payload':dump({'run_id':run['id'],'runtime':frozen['runtime']})}
    assert json.loads(stage_job(store,legacy,'scorer')['payload'])['runtime']==frozen['runtime']
    calls=[]
    class Recorder:
        def execute(self,current,prompt,folder,on_tick=lambda:None,**kwargs):
            payload=json.loads(current['payload']);calls.append((current.get('runtime_role'),payload['runtime'],Path(folder)))
            if current.get('runtime_role')=='evaluator':
                # The draft was admitted before the scorer began.
                assert store.rows('SELECT id FROM briefs WHERE run_id=?',(run['id'],))
                brief=json.loads((folder/'input.json').read_text())['brief']
                (folder/'assessment.json').write_text(dump({'brief_hash':brief['hash'],'summary':'保持限定','overall':'达到要求',
                    **{name:3 for name in ('evidence','coverage','analysis','expression')}}))
            else:
                assert '不在这里调用 Evaluator' in prompt
                (folder/'draft.json').write_text(dump({'title':'交付','markdown':'预计交付。'}));on_tick()
            return {'returncode':0,'runtime':payload['runtime']}
    worker=Worker(store);worker.runtime=Recorder()
    result=worker.generate(job)
    assert [(c[0],c[1]) for c in calls]==[(None,frozen['runtime']),('evaluator',roles['evaluator'])]
    assert calls[0][2]!=calls[1][2]
    assert result['scoring']['runtime']==roles['evaluator']
    # Learning uses this exact generation pipeline, with no redundant single judge.
    trial=store.enqueue('generate',{'run_id':run['id'],'single_evaluation':False})
    calls.clear()
    trial_result=worker.generate(trial,score=False)
    assert len(calls)==1 and calls[0][0] is None and 'scoring' not in trial_result
    # The feedback queue independently freezes current overrides.
    store.set_meta('settings',{**store.settings(),'role_models':roles})
    store.comment(result['version_id'],'明确预计状态')
    learning=enqueue_feedback(store)
    assert json.loads(learning['payload'])['role_models']==roles
    # Actual WikiSkill dispatch picks the phase model, not the writing model.
    learning_calls=[]
    class LearningRecorder:
        def execute(self,current,*args,**kwargs): learning_calls.append(json.loads(current['payload'])['runtime'])
    with patch('briefloop.learning.native_agents.dispatch',return_value={'handoffs':[{'request_id':'synthetic'}]}), \
         patch('briefloop.learning.feedback_loop.work',return_value={'phase':'complete'}), \
         patch('briefloop.learning._sync_wiki'):
        for phase in ('maintainer','proposer'):
            _role(store,LearningRecorder(),learning,tmp_path/'study',1,phase)
    assert learning_calls==[roles['maintainer'],roles['proposer']]
    store.update_job(job['id'],'failed')
    store.set_meta('settings',{**store.settings(),'role_models':{}})
    resumed=Worker(store).resume(job['id'])
    assert resumed['id']!=job['id']
    assert json.loads(resumed['payload'])['role_models']['evaluator']==store.runtime_config()


def test_selected_model_reaches_cli_and_chat_transport_and_rejects_changed_resume(tmp_path):
    store=Store(tmp_path/'workspace')
    selected={'model':'vendor/custom-model','reasoning_effort':None,'model_provider':'configured-responses'}
    job=store.enqueue('learn',{'role_models':{'assessor':selected}})
    stage=stage_job(store,job,'assessor')
    with patch('briefloop.runtime.shutil.which',return_value='/fake/codex'), patch('briefloop.runtime.subprocess.Popen',side_effect=RuntimeError('no model call')) as process:
        with pytest.raises(RuntimeError,match='no model call'):
            CodexRuntime(store).execute(stage,'compare',tmp_path/'cli')
    command=process.call_args.args[0]
    assert 'model="vendor/custom-model"' in command
    assert 'model_provider="configured-responses"' in command
    assert not any('model_reasoning_effort=' in part for part in command)
    class CaptureHarness:
        def __init__(self):self.arguments=None
        def create_session(self,*args):return {'id':'session'}
        def start_internal(self,*args,**kwargs):
            self.arguments=kwargs;raise RuntimeError('no model call')
    capture=CaptureHarness()
    with pytest.raises(RuntimeError,match='no model call'):
        InteractiveRuntime(store,capture).execute(stage,'compare',tmp_path/'chat')
    assert capture.arguments['runtime']=={'model':'vendor/custom-model','effort':None,'model_provider':'configured-responses'}
    changed={**stage,'payload':dump({**json.loads(stage['payload']),'runtime':store.runtime_config()})}
    with pytest.raises(ValueError,match='模型已改变'):
        CodexRuntime(store).execute(changed,'compare',tmp_path/'cli')


def test_evaluator_migration_preserves_settings_and_frozen_legacy_modes(tmp_path):
    astra={'model':'gpt-6-astra','reasoning_effort':'low'}
    other={'model':'gpt-5.6-terra','reasoning_effort':'medium'}
    assert Settings(role_models={'scorer':astra,'assessor':other}).model_dump(exclude_none=True)['role_models']=={'evaluator':astra}
    assert Settings(role_models={'assessor':other}).model_dump(exclude_none=True)['role_models']=={'evaluator':other}
    assert Settings(role_models={'evaluator':other,'scorer':astra}).model_dump(exclude_none=True)['role_models']=={'evaluator':other}
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'role_models':{'scorer':astra,'assessor':other}})
    assert store.settings()['role_models']=={'evaluator':astra}
    assert store.meta('settings')['role_models']=={'scorer':astra,'assessor':other}
    job=store.enqueue('learn',{})
    assert set(json.loads(job['payload'])['role_models'])=={'evaluator','maintainer','proposer'}
    payload=json.loads(job['payload'])
    payload['role_models']={'scorer':astra,'assessor':other,'maintainer':payload['runtime'],'proposer':payload['runtime']}
    with store.tx() as connection:
        connection.execute('UPDATE jobs SET payload=? WHERE id=?',(dump(payload),job['id']))
    old=store.one('jobs',job['id']);frozen=old['payload']
    single=stage_job(store,old,'evaluator',mode='single')
    pairwise=stage_job(store,old,'evaluator',mode='pairwise')
    assert single['runtime_role']==pairwise['runtime_role']=='evaluator'
    assert json.loads(single['payload'])['runtime']==astra
    assert json.loads(pairwise['payload'])['runtime']==other
    assert single['evaluation_mode']=='single' and pairwise['evaluation_mode']=='pairwise'
    store.update_job(job['id'],'failed')
    resumed=Worker(store).resume(job['id'])
    assert resumed['id']!=job['id']
    assert json.loads(resumed['payload'])['role_models']['evaluator']==astra
    assert store.one('jobs',job['id'])['payload']==frozen
    from briefloop.progress import role_label
    assert role_label('Scorer')=='Evaluator · 评分'
    assert role_label('Assessor')=='Evaluator · 比较'


def test_evaluator_prompts_run_directly_in_the_selected_independent_session(tmp_path):
    from briefloop.runtime import assessment_prompt, COMMON
    from briefloop.learning import comparison_prompt
    store=Store(tmp_path/'workspace')
    source=store.add_source('source','计划交付，尚未完成。')
    run=store.create_run({'title':'报告','objective':'保留状态限定'},[source['id']])
    brief=store.publish(run['id'],{'title':'报告','markdown':'计划交付。'})
    folder=store.root/'jobs'/'evaluator-test';folder.mkdir()
    for prompt in (assessment_prompt(store,brief,folder),comparison_prompt(store,folder)):
        assert '已启动的独立 Evaluator 会话' in prompt
        assert '不创建新的 Evaluator 子会话或子 agent' in prompt
        assert 'conversation.json / execution.json' in prompt
        assert '你是 Orchestrator' not in prompt
        assert 'spawn/delegate' not in prompt and 'fork_turns' not in prompt
        assert 'agents.json' not in prompt
    assert 'assessment.json' in assessment_prompt(store,brief,folder)
    assert 'comparison.json' in comparison_prompt(store,folder)
    assert 'spawn/delegate' in COMMON  # Research and learning delegation stays intact.


def test_custom_provider_and_default_effort_freeze_without_changing_old_jobs(tmp_path):
    from briefloop.chat_tools import workspace_action, chat_instructions
    store=Store(tmp_path/'workspace')
    older=store.enqueue('generate',{})
    assert 'model_provider' not in json.loads(older['payload'])['runtime']
    store.set_meta('settings',{**store.settings(),'model':'vendor/writer','model_provider':'local-responses',
        'reasoning_effort':'none','role_models':{'evaluator':{'model':'vendor/judge','model_provider':'','reasoning_effort':''}}})
    config=store.runtime_config()
    assert config=={'model':'vendor/writer','model_provider':'local-responses','reasoning_effort':None}
    current=store.enqueue('generate',{})
    frozen=json.loads(current['payload'])
    assert frozen['runtime']==config and frozen['role_models']['maintainer']==config
    assert frozen['role_models']['evaluator']=={'model':'vendor/judge','reasoning_effort':None}
    assert store.one('jobs',older['id'])['payload']==older['payload']
    source=store.add_source('local','sample')
    runtime={'model':'other/free-model','model_provider':'another-responses','reasoning_effort':None}
    request={'action':'generate','requirements':{'title':'test','objective':'read local'},'source_ids':[source['id']],'runtime':runtime}
    dispatched=workspace_action(store,request)
    assert json.loads(store.one('jobs',dispatched['job_id'])['payload'])['runtime']==runtime
    text=chat_instructions(store,{'model':runtime['model'],'model_provider':runtime['model_provider'],'effort':None})
    example=next(line for line in text.splitlines() if line.startswith('- {"action":"generate"'))
    assert json.loads(example[2:].split('：正式',1)[0])['runtime']==runtime
    from briefloop.runtime import runtime_instruction
    assert '不传 model override' in runtime_instruction(runtime)
