"""Use actual Native Scout tools with synthetic sources and no paid models."""
import json
from pathlib import Path
import threading

import pytest

from briefloop import native_orchestrator, research_plan, scout_coverage
from briefloop.native_harness import NativeHarness
from briefloop.native_roles import run_tool
from briefloop.runtime import Worker, generation_prompt
from test_native_orchestrator import FlowEngine, setup, contract


@pytest.mark.parametrize('layout',['research','job'])
def test_second_round_uses_committed_path_and_reclaims_free_parallel_slots(tmp_path,layout):
    store,source,run,job=setup(tmp_path)
    store.update_settings({'max_parallel':4})
    with store.tx() as connection:
        connection.execute('UPDATE jobs SET payload=? WHERE id=?',
            (json.dumps({**json.loads(job['payload']),'max_parallel':4}),job['id']))
    job=store.one('jobs',job['id'])
    plan=research_plan.freeze(store,run['id'],structure={'breadth':4,'parallel':4,'depth':3})
    assert plan['frozen_runtime']['max_parallel']==4
    scout_coverage.declare(store,run['id'],[])
    research_plan.finish_round(store,run['id'])
    research_plan.begin_round(store,run['id'])
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    worker=Worker(store)
    assert worker.budget.reserve(job['id'],3)
    assert worker.budget.reserve('other-report',9)
    prompt=generation_prompt(store,run,folder,'briefloop-native',scout_budget=worker._scout_budget(job))
    assert json.loads((folder/'input.json').read_text())['max_parallel']==2
    worker.budget.release('other-report')
    config,_=native_orchestrator.prepare(store,job,folder,prompt)
    engine=FlowEngine(store,run,source);engine.scouts=threading.Barrier(4)
    harness=NativeHarness(store,engine)
    parent=harness.create_session(runtime={'model':'fixture/model'})['id']
    config={**config,'native_role':'orchestrator','packet_root':str(folder/'packet'),'session_id':parent,'_harness':harness}
    base=(store.root/'research'/run['id']/'rounds'/'2') if layout=='research' else folder/'round-2'
    tasks=[{'slot_id':f'scout-{i}','assignment':f'Read synthetic source {i}',
            'result_file':str(base/f'scout-{i}'/'result.json')} for i in range(1,5)]
    try:
        # The generic workspace action accepts both ledger layouts. Native must
        # execute the same committed paths rather than silently substitute its own.
        registered=run_tool(store,config,'workspace_action',{'request':{'action':'set_scout_tasks','scout_tasks':tasks}})
        assert registered['ok'],registered
        saved=run_tool(store,config,'save_plan',{'plan':{'summary':'Second round','reader_contract':contract(store,run['id']),'scout_tasks':tasks}})
        assert saved['ok'],saved
        result=run_tool(store,config,'run_scouts',{'tasks':tasks})
        assert result['ok'],result
        receipt=json.loads(result['content'][0]['text'])
        assert receipt['parallel_limit']==4
        assert all(t['status']=='complete' for t in receipt['tasks'])
        assert worker.budget.snapshot()['held'][job['id']]==5
        assert worker.budget.snapshot()['in_use']<=worker.budget.limit
        assert all(Path(t['result_file']).is_file() for t in tasks)
        assert all(t['status']=='complete' for t in scout_coverage.view(store,run['id'])['scout_execution'])
        agents=json.loads((folder/'agents.json').read_text())['agents']
        assert len(agents)==4 and all(a['started'] and a['ended']>=a['started'] for a in agents)
        from briefloop.progress import ProgressTracker
        tracker=ProgressTracker(store,job['id'],folder,context=job)
        tracker.update()
        from briefloop.task_progress import summary
        visible=summary(store,job['id'])['agents']
        assert len(visible)==4 and all(a['started'] and a['ended'] for a in visible)
        if layout=='research':assert not (folder/'round-2').exists()
    finally:
        harness.close()
