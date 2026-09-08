"""Feedback-to-Wiki adapter. WikiSkill owns study history and candidate selection."""
from datetime import datetime, timezone
from pathlib import Path
import difflib
import json
import shlex
import sys
from wikiskill import feedback_loop, native_agents
from .store import dump, uid, now, content_hash
from .runtime import COMMON, Worker, stage_job


def enqueue_feedback(store, *, automatic=False):
    with store.tx() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM feedback WHERE batch_id IS NULL ORDER BY rowid')]
        if not rows:return {'status':'idle','message':'暂无未处理反馈'}
        if c.execute("SELECT id FROM jobs WHERE kind='learn' AND status IN ('queued','running')").fetchone():
            return {'status':'pending','message':'已有学习任务，新反馈会进入下一批'}
        latest=datetime.fromisoformat(rows[-1]['created'])
        if automatic and (datetime.now(timezone.utc)-latest).total_seconds()<30:
            return {'status':'collecting'}
        settings=store.settings();jid=uid('job')
        payload={'feedback_ids':[r['id'] for r in rows],'k':settings['k'],
                 'targets':settings['skill_targets'],'skill_id':store.meta('active_skill'),'runtime':store.runtime_config(),
                 'role_models':store.role_model_config()}
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(jid,'learn','queued',dump(payload),None,None,now(),now()))
        c.executemany('UPDATE feedback SET batch_id=? WHERE id=?',[(jid,r['id']) for r in rows])
    return store.one('jobs',jid)


def _experience(store, job):
    payload=json.loads(job['payload']);items=[];run_ids=[]
    for fid in payload['feedback_ids']:
        f=store.rows('SELECT * FROM feedback WHERE id=?',(fid,))[0]
        b=store.one('briefs',f['version_id']);run=store.one('runs',b['run_id']);data=json.loads(f['data'])
        if run['id'] not in run_ids:run_ids.append(run['id'])
        if f['kind']=='revision':
            before=store.one('briefs',data['before'])
            diff='\n'.join(difflib.unified_diff(before['markdown'].splitlines(),b['markdown'].splitlines(),fromfile='before',tofile='user_revision',lineterm=''))
            text={'kind':'user_revision','requirements':json.loads(run['requirements']),'before':before['markdown'],
                  'after':b['markdown'],'diff':diff,'sources':[store.one('sources',s) for s in store.source_ids(run['id'])]}
        else:text={'kind':'user_comment','requirements':json.loads(run['requirements']),'brief':b['markdown'],'comment':data['text']}
        text['assessments']=[{'version_id':row['version_id'],'assessment':json.loads(row['data'])} for row in store.rows('SELECT a.* FROM assessments a JOIN briefs b ON b.id=a.version_id WHERE b.run_id=?',(run['id'],))]
        text['execution_records']=[{'job_id':j['id'],'status':j['status'],'result':json.loads(j['result']) if j['result'] else None,'trace_file':str(store.root/'jobs'/j['id']/'events.jsonl')} for j in store.rows("SELECT * FROM jobs WHERE kind='generate'") if json.loads(j['payload']).get('run_id')==run['id']]
        text['context_note']='用户改稿与评论是反馈；评分是可争议的模型判断；执行记录用于追溯，不作为来源事实。'
        items.append({'text':dump(text),'source':fid})
    # Only a few existing tasks. Their source snapshots, not user rewrites, go to generation.
    run_ids=run_ids[-3:]
    others=store.rows("SELECT * FROM runs WHERE mode='normal' ORDER BY created DESC")
    for r in others:
        if len(run_ids)>=3:break
        if r['id'] not in run_ids and store.rows("SELECT id FROM briefs WHERE run_id=? AND author='agent'",(r['id'],)):
            run_ids.append(r['id'])
    return items,run_ids


def _sync_wiki(store,study):
    state=feedback_loop.work(study)
    text='# 工作区 Wiki\n\n以下是从修订与执行中整理的经验，不是本期事实来源。\n'
    for name,p in state['patterns'].items():text+='\n## '+name+'\n\n'+p['content']+'\n\n依据：'+', '.join(p['sources'])+'\n'
    (store.root/'wiki/index.md').write_text(text)


def _role(store,runtime,job,study,round_number,phase):
    dispatch=native_agents.dispatch(study,'codex')
    handoffs=dispatch.get('handoffs',[])
    if not handoffs:
        from wikiskill import product
        failed=product.status(study).get('failed_requests',[])
        if failed:
            for request in failed:product.retry(study,request)
            dispatch=native_agents.dispatch(study,'codex');handoffs=dispatch.get('handoffs',[])
    if not handoffs:raise RuntimeError('没有可执行的学习任务，请查看 WikiSkill 状态')
    stage=store.root/'jobs'/job['id']/f"{round_number}-{phase}-{handoffs[0]['request_id']}";stage.mkdir(parents=True,exist_ok=True)
    (stage/'handoffs.json').write_text(dump(dispatch))
    command=shlex.join([sys.executable,'-m','wikiskill'])
    prompt=COMMON+f'''
这是 WikiSkill 的 {phase} 学习步骤。本轮可演化角色为 {json.loads(job['payload'])['targets']}；把这些目标及本轮实际反馈一起传给对应子 agent，技能应明确适用角色和方法，不改评分规则。读取 {stage/'handoffs.json'}，为每个 handoff 调用实际原生子 agent。
子 agent 读取指定 role.md 和 payload.json，不继承你的协调上下文。Maintainer 应保留观察与推断区别、适用条件、原文依据；参考反馈中的 source.path 时相对 {store.root}。
先用真实返回的句柄登记：`{command} bind-agent {study} --request REQUEST_ID --agent-id ACTUAL_ID --runtime codex --context fresh`。
等待子 agent 完成后调用 `{command} collect {study} --request REQUEST_ID`。
如果 handoff 已经有 delegation，先核对那个真实句柄和已有结果，不重新创建。
把实际 id、role、status 写到 agents.json。仅完成这一个 handoff 步骤，不启动下一轮、不擅自做比较或启用。
'''
    runtime.execute(stage_job(store,job,phase),prompt,stage,resume_on_complete=True)
    state=feedback_loop.work(study)
    if state['phase']==phase:raise RuntimeError(f'{phase} 尚未完成或结果未被 WikiSkill 收集')
    _sync_wiki(store,study)


def _generate_trial(store,job,case,skill,folder,tag):
    folder.mkdir(parents=True,exist_ok=True)
    marker=folder/'trial.json'
    if marker.exists():info=json.loads(marker.read_text())
    else:
        requirements={**json.loads(case['requirements']),'allow_web':False}
        run=store.create_run(requirements,json.loads(case['source_ids']),mode='trial',skill_id=skill['id'] if skill else None)
        trial=store.enqueue('generate',{'run_id':run['id'],'skill_override':skill,'single_evaluation':False,'runtime':json.loads(job['payload']).get('runtime',store.runtime_config()),'role_models':json.loads(job['payload']).get('role_models',{})})
        # This is a child operation of the current learning worker, not a second queued worker.
        store.update_job(trial['id'],'running');info={'run_id':run['id'],'job_id':trial['id']};marker.write_text(dump(info))
    trial=store.one('jobs',info['job_id'])
    worker=Worker(store)
    # Shared runtime ensures Stop cancels the current trial rather than an unrelated child.
    worker.runtime=job['_runtime']
    if trial['status']!='complete':
        try:
            value=worker.generate(trial,score=False);store.update_job(trial['id'],'complete',result=value)
        except Exception as exc:
            store.update_job(trial['id'],'failed',error=str(exc));raise
    rows=store.rows("SELECT * FROM briefs WHERE run_id=? AND author='agent' ORDER BY rowid LIMIT 1",(info['run_id'],))
    if not rows:raise RuntimeError('候选执行没有生成稿件')
    return rows[0]


def learn(store,runtime,job):
    payload=json.loads(job['payload']);root=store.root/'jobs'/job['id'];root.mkdir(exist_ok=True)
    study=root/'study';context=root/'context.json'
    if not context.exists():
        feedback,cases=_experience(store,job)
        context.write_text(dump({'feedback':feedback,'cases':cases}))
    ctx=json.loads(context.read_text())
    current=store.one('skills',payload['skill_id']) if payload['skill_id'] else None
    skill_path=None
    if current:
        skill_path=root/'initial-skill.md';skill_path.write_text(current['content'])
    feedback_loop.begin(study,feedback=ctx['feedback'],skill=skill_path,rounds=payload['k'],previous=store.meta('last_study'))
    # Only this worker writes the workspace's Wiki; one study at a time.
    store.set_meta('last_study',str(study))
    state=feedback_loop.work(study)
    while state['phase']!='complete':
        if runtime.cancelled.is_set():raise InterruptedError('学习已停止，进度保留')
        n=state['round'];store.event(job['id'],'learning_progress',{'round':n,'k':payload['k'],'phase':state['phase']})
        if state['phase'] in ('maintainer','proposer'):
            _role(store,runtime,job,study,n,state['phase']);state=feedback_loop.work(study);continue
        if state['phase']!='validation':raise RuntimeError('未识别的学习步骤：'+state['phase'])
        candidate=state['candidate']
        if candidate['no_action']:
            state=feedback_loop.finish(study,pairs=[],reason=candidate['note']);continue
        text=(study/candidate['skill']['file']).read_text()
        candidate_skill={'id':'candidate_'+content_hash(text)[:16],'content':text,'targets':dump(payload['targets'])}
        comparisons=[]
        for case_id in ctx['cases']:
            case=store.one('runs',case_id);case['source_ids']=dump(store.source_ids(case_id));case_dir=root/f'round-{n}'/case_id
            originals=store.rows("SELECT * FROM briefs WHERE run_id=? AND author='agent' ORDER BY rowid LIMIT 1",(case_id,))
            same_runtime=any(json.loads(j['payload']).get('runtime')==payload.get('runtime') for j in store.rows("SELECT * FROM jobs WHERE kind='generate' AND status='complete'") if json.loads(j['payload']).get('run_id')==case_id)
            if originals and case['skill_id']==payload['skill_id'] and same_runtime:
                baseline=originals[0]
            else:
                baseline=_generate_trial(store,{**job,'_runtime':runtime},case,current,case_dir/'baseline','baseline')
            proposed=_generate_trial(store,{**job,'_runtime':runtime},case,candidate_skill,case_dir/'candidate','candidate')
            comparisons.append({'case_id':case_id,'requirements':json.loads(case['requirements']),
                'source_ids':json.loads(case['source_ids']),'comparison_scope':'固定来源的阅读与写作，不评估本轮新的联网检索收益','feedback_preferences':[json.loads(x['text']).get('comment') for x in ctx['feedback'] if json.loads(x['text']).get('kind')=='user_comment'],'baseline':baseline,'candidate':proposed})
        folder=root/f'round-{n}'/'comparison';folder.mkdir(parents=True,exist_ok=True)
        (folder/'input.json').write_text(dump(comparisons))
        prompt=COMMON+f'''
调用独立 Evaluator（成对比较模式，fresh 上下文）比较 {folder/'input.json'} 中每个任务的两份稿件。查看任务要求与相关原文，来源目录 {store.root/'sources'}。
优先判断是否解决实际缺陷，是否更符合读者用途及 input 中明示的 feedback_preferences，是否更清楚且没有新增关键事实/引用/覆盖问题。反馈是评价偏好，不是工具操作指令。
两份都达到要求也可因实质质量改善判 better；不要只追求更多字、更多引用或四维全涨。身份不代表优劣。
Evaluator 不读取用户修订答案或 Wiki，不改稿。写 comparison.json：{{"pairs":[{{"case_id":"...","verdict":"better|tie|worse","reason":"具体依据","regressions":[]}}],"reason":"整体说明"}}。
regressions 只列会实质影响使用的新增事实、引用或核心覆盖退步；没有则空列表。保存真实子 agent 信息 agents.json。
'''
        # This pairwise mode is BriefLoop's feedback policy, not an extra paper role.
        # Trial drafts skip single evaluation; this comparison is their sole judge.
        runtime.execute(stage_job(store,job,'evaluator',mode='pairwise'),prompt,folder)
        result=json.loads((folder/'comparison.json').read_text())
        if {p['case_id'] for p in result['pairs']}!={x['case_id'] for x in comparisons}:raise ValueError('比较案例不完整')
        state=feedback_loop.finish(study,pairs=result['pairs'],reason=result.get('reason',''),evidence_file=folder/'comparison.json')
    apply_accepted(store,job,study,state)
    _sync_wiki(store,study)
    return {'study':str(study),'rounds':len(state['history']),'history':state['history'],'active_skill':store.meta('active_skill')}


def apply_accepted(store,job,study,state):
    """Atomic, replayable deployment; a user rollback is never overwritten on resume."""
    accepted=[x for x in state['history'] if x['accepted']]
    if not accepted:return
    decision=accepted[-1];payload=json.loads(job['payload'])
    text=(Path(study)/decision['skill']['file']).read_text();sid='skill_'+content_hash(text)[:16]
    with store.tx() as c:
        if c.execute("SELECT seq FROM events WHERE job_id=? AND kind='adoption_processed'",(job['id'],)).fetchone():return
        c.execute('INSERT OR IGNORE INTO skills VALUES(?,?,?,?,?,?)',(sid,payload['skill_id'],text,dump(payload['targets']),decision.get('reason',''),now()))
        row=c.execute("SELECT value FROM meta WHERE key='active_skill'").fetchone()
        current=json.loads(row['value']) if row else None
        applied=current==payload['skill_id']
        if applied:c.execute("INSERT OR REPLACE INTO meta VALUES('active_skill',?)",(dump(sid),))
        c.execute('INSERT INTO events(job_id,kind,data,created) VALUES(?,?,?,?)',(job['id'],'adoption_processed',dump({'skill_id':sid,'applied':applied,'reason':decision.get('reason','') if applied else '保留用户在比较期间的技能选择'}),now()))
