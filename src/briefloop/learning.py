"""Feedback-to-Wiki adapter. WikiSkill owns study history and candidate selection."""
from datetime import datetime, timezone
from pathlib import Path
import difflib
import json
from .agent_commands import agent_command, quote_path
from wikiskill import feedback_loop, native_agents
from .store import dump, uid, now, content_hash
from .learning_budget import LearningAuthorizationRequired
from .runtime import COMMON, COMMON_OPENCODE, EVALUATOR_CONTEXT, Worker, stage_job


def enqueue_feedback(store, *, automatic=False, confirmed_plan=None):
    with store.tx() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM feedback WHERE batch_id IS NULL ORDER BY rowid')]
        if not rows:return {'status':'idle','message':'暂无未处理反馈'}
        # One batch learns one scope: the workspace, or one saved reader (#858).
        # The oldest waiting feedback decides; other scopes wait for the next batch.
        scope=lambda row:json.loads(row['data']).get('reader_id') if row['kind']=='revision_edit' else None
        reader_id=scope(rows[0]);rows=[r for r in rows if scope(r)==reader_id]
        if c.execute("SELECT id FROM jobs WHERE kind='learn' AND status IN ('queued','running')").fetchone():
            return {'status':'pending','message':'已有学习任务，新反馈会进入下一批'}
        latest=datetime.fromisoformat(rows[-1]['created'])
        settings=store.settings()
        from .learning_budget import authorization, automatic_allowed, plan
        from .readers import skill_for as reader_skill
        # Worker idleness, a reopened page or an agent request never stands in for
        # the user's confirmation, and the plan is re-checked inside this batch.
        if automatic and not automatic_allowed(settings):
            return {'status':'not_authorized','message':'自动学习未获确认，反馈已保存'}
        if automatic and (datetime.now(timezone.utc)-latest).total_seconds()<30:
            return {'status':'collecting'}
        record=authorization(settings,'automatic' if automatic else 'manual',confirmed=confirmed_plan)
        jid=uid('job')
        payload={'feedback_ids':[r['id'] for r in rows],'k':settings['k'],'budget':plan(settings),'authorization':record,
                 'targets':settings['skill_targets'],'skill_id':reader_skill(store,reader_id),'runtime':store.runtime_config(),
                 **({'reader_id':reader_id} if reader_id else {}),
                 'role_models':store.role_model_config(),'agent_backend':settings.get('agent_backend','codex')}
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(jid,'learn','queued',dump(payload),None,None,now(),now()))
        c.executemany('UPDATE feedback SET batch_id=? WHERE id=?',[(jid,r['id']) for r in rows])
    store.wake_jobs()
    return store.one('jobs',jid)


def _experience(store, job):
    from . import revision_edits
    payload=json.loads(job['payload']);items=[];run_ids=[]
    for fid in payload['feedback_ids']:
        f=store.rows('SELECT * FROM feedback WHERE id=?',(fid,))[0]
        b=store.one('briefs',f['version_id']);run=store.one('runs',b['run_id']);data=json.loads(f['data'])
        if f['kind']=='revision' or f['kind']=='revision_edit':
            # Split revisions pass only their taste/reader-specific edits (#858):
            # fact corrections and unanswered questions never become writing feedback.
            edits=revision_edits.edits_by_id(store,data['edit_ids']) if f['kind']=='revision_edit' else revision_edits.learnable(store,fid)
            if edits is not None and not edits:continue
        if run['id'] not in run_ids:run_ids.append(run['id'])
        if f['kind']=='revision_edit':
            text={'kind':'user_revision_edits','requirements':json.loads(run['requirements']),'brief':b['markdown'],
                  'edits':[{k:e[k] for k in ('op','heading','before','after','category','decided_by')} for e in edits],
                  'sources':[store.one('sources',s) for s in store.source_ids(run['id'])]}
        elif f['kind']=='revision' and edits is not None:
            before=store.one('briefs',data['before'])
            text={'kind':'user_revision_edits','requirements':json.loads(run['requirements']),'before':before['markdown'],
                  'edits':[{k:e[k] for k in ('op','heading','before','after','category','decided_by')} for e in edits],
                  'excluded':'事实纠错与待用户确认的改动不在此列，不作为写作经验',
                  'sources':[store.one('sources',s) for s in store.source_ids(run['id'])]}
        elif f['kind']=='revision':
            before=store.one('briefs',data['before'])
            diff='\n'.join(difflib.unified_diff(before['markdown'].splitlines(),b['markdown'].splitlines(),fromfile='before',tofile='user_revision',lineterm=''))
            text={'kind':'user_revision','requirements':json.loads(run['requirements']),'before':before['markdown'],
                  'after':b['markdown'],'diff':diff,'sources':[store.one('sources',s) for s in store.source_ids(run['id'])]}
        elif f['kind']=='review_correction':
            before=store.one('briefs',data['before'])
            text={**data}
            # New verified feedback retains the Review packet's exact evidence,
            # requirements and versions. Preserve older feedback compatibly.
            text.setdefault('requirements',json.loads(run['requirements']))
            text.setdefault('before_text',before['markdown']);text.setdefault('after_text',b['markdown'])
        else:text={'kind':'user_comment','requirements':json.loads(run['requirements']),'brief':b['markdown'],'comment':data['text']}
        if 'assessments' not in text:
            text['assessments']=[{'version_id':row['version_id'],'assessment':json.loads(row['data'])} for row in store.rows('SELECT a.* FROM assessments a JOIN briefs b ON b.id=a.version_id WHERE b.run_id=?',(run['id'],))]
        if 'execution_records' not in text:
            text['execution_records']=[{'job_id':j['id'],'status':j['status'],'result':json.loads(j['result']) if j['result'] else None,'trace_file':str(store.root/'jobs'/j['id']/'events.jsonl')} for j in store.rows("SELECT * FROM jobs WHERE kind='generate'") if json.loads(j['payload']).get('run_id')==run['id']]
        text['context_note']='用户改稿与评论是反馈；review_correction仅表示独立复核过的处理，不把来源正常更新当原稿事实错误。评分仍是可争议的模型判断；执行记录用于追溯，不作为来源事实。'
        items.append({'text':dump(text),'source':fid,'origin':'automatic' if f['kind']=='review_correction' else 'human',
                      'learning_intent':data.get('learning_intent','feedback') if f['kind']=='comment' else 'feedback'})
    # Only a few existing tasks. Their source snapshots, not user rewrites, go to generation.
    run_ids=run_ids[-3:]
    others=store.rows("SELECT * FROM runs WHERE mode='normal' ORDER BY created DESC")
    reader_id=payload.get('reader_id')
    for r in others:
        if len(run_ids)>=3:break
        # A reader's batch is validated only on reports written for that reader.
        if reader_id and (json.loads(r['requirements']).get('reader_profile') or {}).get('id')!=reader_id:continue
        if r['id'] not in run_ids and store.rows("SELECT id FROM briefs WHERE run_id=? AND author='agent'",(r['id'],)):
            run_ids.append(r['id'])
    return items,run_ids


def _sync_wiki(store,study,reader_id=None):
    from .readers import scope_meta,wiki_path,profile
    if store.meta(scope_meta('last_study',reader_id))!=str(study):
        raise ValueError('已有更新的学习记录；旧任务不能覆盖当前 Wiki')
    state=feedback_loop.work(study)
    text=('# 工作区 Wiki\n\n以下是从修订与执行中整理的经验，不是本期事实来源。\n' if not reader_id else
          '# 读者 Wiki：'+profile(store,reader_id,active_only=False)['name']+'\n\n只用于写给这位读者的报告；不是本期事实来源。\n')
    explicit=[x for x in state['feedback'] if x.get('learning_intent')=='explicit_requirement' and x.get('origin')=='human']
    if explicit:
        text+='\n## 人类明确要求（持续保留）\n'
        for item in explicit:
            value=json.loads(item['text'])
            text+='\n- '+value.get('comment',item['text'])+'（来源：'+str(item.get('source'))+'）\n'
        if state['history'] and state['history'][-1].get('requirements_pending'):
            text+='\n技能待完善：人类要求保留，候选需要修改后继续验证；本轮未采纳。\n'
    text+='\n反馈来源：'+', '.join(str(x.get('source'))+' ['+('人类明确要求' if x.get('learning_intent')=='explicit_requirement' else '自动发现' if x.get('origin')=='automatic' else '人类反馈')+']' for x in state['feedback'])+'\n'
    for name,p in state['patterns'].items():text+='\n## '+name+'\n\n'+p['content']+'\n\n依据：'+', '.join(p['sources'])+'\n'
    destination=wiki_path(store,reader_id);destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix('.tmp');temporary.write_text(text,encoding='utf-8');temporary.replace(destination)
    from .notifications import wiki_changed
    wiki_changed(store,text)


def study_runtime(study, default='codex'):
    """The host a study records its learning children under. New studies name
    the real backend; a study started before that keeps its original tag so it
    still resumes."""
    from wikiskill import product
    if not (Path(study)/'config.json').exists():return default
    return product._load(Path(study).resolve())['config'].get('agent_runtime') or default


def _role(store,runtime,job,study,round_number,phase):
    host=study_runtime(study,json.loads(job['payload']).get('agent_backend','codex'))
    dispatch=native_agents.dispatch(study,host)
    handoffs=dispatch.get('handoffs',[])
    if not handoffs:
        from wikiskill import product
        failed=product.status(study).get('failed_requests',[])
        if failed:
            for request in failed:product.retry(study,request)
            dispatch=native_agents.dispatch(study,host);handoffs=dispatch.get('handoffs',[])
    if not handoffs:raise RuntimeError('没有可执行的学习任务，请查看 WikiSkill 状态')
    stage=store.root/'jobs'/job['id']/f"{round_number}-{phase}-{handoffs[0]['request_id']}";stage.mkdir(parents=True,exist_ok=True)
    (stage/'handoffs.json').write_text(dump(dispatch),encoding='utf-8')
    from .backends import validate_backend
    backend=validate_backend(json.loads(job['payload']).get('agent_backend','codex'))
    if backend=='briefloop-native':
        # No coordinator turn: the runner dispatches the handoff to one native
        # session with a frozen copy of it, and binds/collects it on submit.
        from .native_roles import learning_packet
        handoff=handoffs[0]
        learning_packet(store,handoff,stage)
        submit='submit_patterns' if phase=='maintainer' else 'submit_proposal'
        prompt=(f"这是 WikiSkill 的 {phase} 学习步骤。本轮可演化角色为 {json.loads(job['payload'])['targets']}；技能应明确适用角色和方法，不改评分规则。\n"
                "角色说明在任务包的 role.md，任务在 payload.json 及其 learning-context.json（路径均为任务包内相对路径）。"
                +('Maintainer 应保留观察与推断区别、适用条件、原文依据。' if phase=='maintainer' else
                  '候选技能写给上述角色在报告任务中使用；本会话的 packet_* 工具只是你读取学习材料的方式，不是该角色的工具，技能里不要写这些工具名，也不要假定该角色只有只读工具。')
                +f"\n完成后调用 {submit} 提交；未通过时按返回的错误修正后重新提交，不要把结果写进回复正文。")
        staged={**stage_job(store,job,phase),'native_packet':{'role':phase,'study':str(study),'request_id':handoff['request_id']}}
        runtime.execute(staged,prompt,stage,resume_on_complete=True)
        state=feedback_loop.work(study)
        if state['phase']==phase:raise RuntimeError(f'{phase} 尚未完成或结果未被 WikiSkill 收集')
        _sync_wiki(store,study,json.loads(job['payload']).get('reader_id'))
        return
    command=agent_command('wikiskill',backend=backend)
    common=COMMON if backend=='codex' else COMMON_OPENCODE
    # The study records the real host (study_runtime); child ids are the
    # actual handles the host returns.
    prompt=common+f'''
这是 WikiSkill 的 {phase} 学习步骤。本轮可演化角色为 {json.loads(job['payload'])['targets']}；把这些目标及本轮实际反馈一起传给对应子 agent，技能应明确适用角色和方法，不改评分规则。读取 {stage/'handoffs.json'}，为每个 handoff 调用实际原生子 agent。
子 agent 读取指定 role.md 和 payload.json，不继承你的协调上下文。Maintainer 应保留观察与推断区别、适用条件、原文依据；参考反馈中的 source.path 时相对 {store.root}。
先用真实返回的句柄登记：`{command} bind-agent {quote_path(study,backend)} --request REQUEST_ID --agent-id ACTUAL_ID --runtime {host} --context fresh`。
等待子 agent 完成后调用 `{command} collect {quote_path(study,backend)} --request REQUEST_ID`。
如果 handoff 已经有 delegation，先核对那个真实句柄和已有结果，不重新创建。
把实际 id、role、status 写到 agents.json。仅完成这一个 handoff 步骤，不启动下一轮、不擅自做比较或启用。
'''
    runtime.execute(stage_job(store,job,phase),prompt,stage,resume_on_complete=True)
    state=feedback_loop.work(study)
    if state['phase']==phase:raise RuntimeError(f'{phase} 尚未完成或结果未被 WikiSkill 收集')
    _sync_wiki(store,study,json.loads(job['payload']).get('reader_id'))


def validated_workflow(value):
    """Verify the stored content, not just its advertised identity."""
    import hashlib
    if not isinstance(value,dict):raise ValueError('学习方法快照无效')
    body={key:part for key,part in value.items() if key!='content_hash'}
    digest=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if value.get('content_hash')!=digest:raise ValueError('学习方法快照内容与哈希不一致')
    if not all(isinstance(value.get('role_instructions',{}).get(role),str) for role in ('planning','writing','evaluation')):
        raise ValueError('学习方法快照缺少角色方法')
    return value


def _conditions(store,case,payload):
    import hashlib
    requirements={**json.loads(case['requirements']),'allow_web':False,'fact_check':False}
    validated_workflow(requirements['workflow_snapshot'])
    root=Path(__file__).parent
    names=('store.py','runtime.py','deliverable_spec.py','models.py','learning.py','chat_tools.py',
           'document_workflows.py','agent_commands.py','harness.py','opencode_harness.py','bridge_harness.py',
           'static/runtime-bridge.mjs')
    if payload.get('agent_backend') == 'briefloop-native':
        names += ('native_harness.py','native_roles.py','native_orchestrator.py','analyst.py','scout.py','agent_prompts.py','static/native-engine.mjs')
        names += tuple(str(p.relative_to(root)) for p in sorted((root/'prompt_assets').glob('*.md')))
    return {'schema':1,'requirements':requirements,
            'runtime':payload.get('runtime',store.runtime_config()),'role_models':payload.get('role_models',{}),
            'agent_backend':payload.get('agent_backend',store.settings().get('agent_backend','codex')),
            'common_code':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names},
            'max_parallel':store.settings()['max_parallel'],'additional_roles':store.meta('additional_roles',{}),
            'comparison_policy':'lightweight_pairwise_v1','allow_web':False}


def _prepare_case(store,case,payload,folder):
    from wikiskill.product import write
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);record=folder/'conditions.json'
    if record.exists():
        saved=json.loads(record.read_text(encoding='utf-8'));origin=store.one('runs',saved['origin_run_id'])
    else:
        origin=store.one('runs',case.get('learning_origin_id',case['id']))
        if not json.loads(origin['requirements']).get('workflow_snapshot'):
            # A legacy case gets one trusted anchor for BOTH arms, never a new
            # installed snapshot independently frozen by each trial.
            origin=store._create_learning_run(case['id'],case['learning_source_ids'],skill_id=case.get('skill_id'))
        saved={'origin_run_id':origin['id'],'conditions':_conditions(store,origin,payload)}
        write(record,saved,immutable=True)
    conditions=_conditions(store,origin,payload)
    if saved['conditions']!=conditions or (case.get('learning_conditions') is not None and case['learning_conditions']!=conditions):raise ValueError('学习比较条件已变化，旧尝试保留；请创建新的学习任务')
    original=json.loads(case['requirements']).get('workflow_snapshot')
    if original and validated_workflow(original)!=conditions['requirements']['workflow_snapshot']:
        raise ValueError('学习方法快照已变化，不能续跑旧比较')
    return {**case,'requirements':dump(conditions['requirements']),'learning_origin_id':origin['id'],'learning_conditions':conditions}


def _eligible_cases(store,ids):
    cases=[];skipped=[]
    for case_id in ids:
        case=store.one('runs',case_id);evidence=[]
        if not store.rows("SELECT id FROM briefs WHERE run_id=? AND author='agent' LIMIT 1",(case_id,)):
            # An imported previous report has no BriefLoop draft to compare against (#858).
            skipped.append({'case_id':case_id,'reason':'往期导入报告没有 BriefLoop 生成稿，不做试写比较'})
            continue
        for sid in store.source_ids(case_id):
            provenance=store.root/'sources'/(sid+'.provenance.json')
            metadata=json.loads(provenance.read_text(encoding='utf-8')) if provenance.is_file() else {}
            if case_id in metadata.get('revision_for_runs',[]) or metadata.get('usage')=='revision_feedback':continue
            evidence.append(sid)
        if not evidence:
            skipped.append({'case_id':case_id,'reason':'过滤修订答案后没有有效来源，本案例不可比较'})
            continue
        cases.append({**case,'source_ids':dump(evidence),'learning_source_ids':evidence})
    return cases,skipped


class LearningBudgetExhausted(RuntimeError):
    code='learning_budget_exhausted'


def _budget(payload):
    """Budget frozen at enqueue; older batches derive the same bound from their k."""
    from .learning_budget import MAX_CASES, TRIALS_PER_CASE, max_rounds
    frozen=payload.get('budget') or {}
    rounds=frozen.get('rounds_with_explicit_requirement',max_rounds(payload.get('k',1)))
    return {'rounds_with_explicit_requirement':rounds,
            'max_trial_generations':frozen.get('max_trial_generations',MAX_CASES*TRIALS_PER_CASE*rounds)}


def _generate_trial(store,job,case,skill,folder,tag):
    from .review_learning import source_snapshot
    selected=case.get('learning_source_ids',store.source_ids(case['id']))
    expected=source_snapshot(store,case['id'],source_ids=selected)
    if not selected:raise ValueError('过滤修订答案后没有有效来源，本案例不可比较')
    parent=json.loads(job['payload'])
    case=_prepare_case(store,{**case,'learning_source_ids':selected},parent,folder.parent)
    conditions=case['learning_conditions']
    folder.mkdir(parents=True,exist_ok=True)
    marker=folder/'trial.json'
    if marker.exists():
        info=json.loads(marker.read_text(encoding='utf-8'))
        if info.get('source_snapshot')!=expected:
            raise ValueError('学习验证的来源快照已变化，旧阶段保留；请基于新材料创建新学习任务')
        if info.get('conditions')!=conditions or info.get('skill')!=skill:
            raise ValueError('学习比较条件或技能已变化，不能复用旧阶段')
    else:
        # Hard cap from the budget frozen when the batch was queued: a resume or a
        # repeated round cannot start more trial generations than were confirmed.
        started=sum(1 for _ in (store.root/'jobs'/job['id']).rglob('trial.json')) if job.get('id') else 0
        if started>=_budget(parent)['max_trial_generations']:
            raise LearningBudgetExhausted(f'学习验证已达到确认的试写上限（{started} 次），未启用候选；反馈与已完成的试写保留')
        run=store._create_learning_run(case['learning_origin_id'],selected,skill_id=skill['id'] if skill else None)
        parent=json.loads(job['payload'])
        def own(connection,jid,_payload):
            # The learning job runs this trial itself. Claiming it inside the same
            # transaction leaves no queued window for the report dispatcher, which
            # since #728 keeps running while learning works (#747 review F1).
            connection.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(now(),jid))
        trial=store.enqueue('generate',{'run_id':run['id'],'skill_override':skill,'single_evaluation':False,'inline_owner_job_id':job['id'],'runtime':parent.get('runtime',store.runtime_config()),'role_models':parent.get('role_models',{}),'agent_backend':parent.get('agent_backend',store.settings().get('agent_backend','codex'))},before_commit=own)
        info={'run_id':run['id'],'job_id':trial['id'],'source_snapshot':expected,'conditions':conditions,'skill':skill}
        from wikiskill.product import write
        write(marker,info,immutable=True)
    trial=store.one('jobs',info['job_id'])
    if source_snapshot(store,info['run_id'])!=expected:
        raise ValueError('保存的学习验证来源与本次案例不同，不能复用或续跑')
    actual=store.one('runs',info['run_id'])
    if _conditions(store,actual,parent)!=conditions:raise ValueError('保存的学习方法或执行条件不一致')
    trial_payload=json.loads(trial['payload'])
    for key in ('runtime','role_models','agent_backend','max_parallel'):
        if trial_payload.get(key)!=conditions[key]:raise ValueError('保存的学习宿主或模型条件不一致')
    worker=Worker(store)
    # Shared runtime ensures Stop cancels the current trial rather than an unrelated child.
    worker.runtime=job['_runtime']
    if trial['status']!='complete':
        try:
            value=worker.generate(trial,score=False);value['learning_conditions']=conditions
            store.update_job(trial['id'],'complete',result=value)
        except Exception as exc:
            store.update_job(trial['id'],'failed',error=str(exc));raise
    saved=store.one('jobs',info['job_id']);result=json.loads(saved['result'] or '{}')
    if result.get('learning_conditions')!=conditions:raise ValueError('学习验证缺少实际比较条件记录')
    if _attempt_source_snapshot(store,saved,result)!=expected:
        raise ValueError('学习验证未保存一致的来源快照，不能比较该稿件')
    vid=result.get('version_id')
    if not vid or not store.generated_by(vid,saved['id']):raise RuntimeError('候选执行没有返回其实际生成版本')
    brief=store.one('briefs',vid)
    if brief['run_id']!=info['run_id']:raise RuntimeError('候选返回版本不属于本次学习验证')
    return brief


def comparison_errors(result,comparisons):
    """What WikiSkill's gate (feedback_loop.finish) and learn() would refuse in a
    comparison result, checked before it is saved: one pair per case, a verdict
    and explicit regressions for each, and a complete evidence-backed check of
    every explicit human requirement."""
    if not isinstance(result,dict) or not isinstance(result.get('pairs'),list):return '结果需要 pairs 列表'
    expected=[x['case_id'] for x in comparisons];pairs=result['pairs']
    seen=[x.get('case_id') for x in pairs if isinstance(x,dict)]
    if len(seen)!=len(pairs) or sorted(seen)!=sorted(expected):
        return f'pairs 必须对每个案例各给一条，case_id 为 {expected}'
    for pair in pairs:
        if pair.get('verdict') not in ('better','tie','worse'):return f"{pair['case_id']}：verdict 只能是 better、tie 或 worse"
        if not isinstance(pair.get('regressions'),list):return f"{pair['case_id']}：regressions 需要列表（没有退步时为空列表）"
        if not isinstance(pair.get('reason'),str) or not pair['reason'].strip():return f"{pair['case_id']}：reason 需要写明具体依据"
        case=next(x for x in comparisons if x['case_id']==pair['case_id'])
        required={x['source'] for x in case.get('explicit_requirements') or []}
        if required:
            checks=pair.get('requirement_checks')
            if (not isinstance(checks,list) or len(checks)!=len(required) or {x.get('source') for x in checks if isinstance(x,dict)}!=required
                or any(type(x.get('fulfilled')) is not bool or not isinstance(x.get('evidence'),str) or not x['evidence'].strip() for x in checks)):
                return f"{pair['case_id']}：requirement_checks 需要对每条明确要求 {sorted(required)} 各给 source、fulfilled（true/false）与具体 evidence"
    return None


def comparison_prompt(store,folder,backend='codex'):
    """The dedicated Evaluator session judges directly; it is already independent."""
    no_question='本轮没有任何用户在旁可问：不要调用宿主的提问或等待授权的工具；遇到含糊之处自行按任务目标决断，并在结果中记录假设。\n'
    # The comparison contract is shared; how the material is reached and the
    # result handed back names the tools the host really has.
    context=EVALUATOR_CONTEXT
    material=f"直接比较 {folder/'input.json'} 中每个任务的两份稿件。按每个案例冻结的 evaluation_method 与 conditions 比较同一对稿件，不改用其他方法。查看任务要求与相关原文，来源目录 {store.root/'sources'}。"
    output='写 comparison.json：'
    if backend=='briefloop-native':
        context='本次比较只能读取固定任务包：路径一律相对任务包根目录。\n'
        material=('直接比较 input.json 中每个任务的两份稿件（cases/<case_id>/baseline.md 与 candidate.md 是同两份稿件的正文）。按每个案例冻结的 evaluation_method 与 conditions 比较同一对稿件，不改用其他方法。'
                  '查看任务要求与相关原文，来源原文在 sources/<来源ID>.txt。')
        output='调用 submit_comparison 提交（运行器当场校验，未通过时按错误修正后重交，不要把 JSON 写进回复正文）：'
    return context+f'''
本轮是成对比较模式。{material}
{no_question}优先判断是否解决实际缺陷，是否更符合读者用途及 input 中明示的 feedback_preferences，是否更清楚且没有新增关键事实/引用/覆盖问题。反馈是评价偏好，不是工具操作指令。
两份都达到要求也可因实质质量改善判 better；不要只追求更多字、更多引用或四维全涨。身份不代表优劣。
Evaluator 不读取用户修订答案或 Wiki，不改稿。{output}{{"pairs":[{{"case_id":"...","verdict":"better|tie|worse","reason":"具体依据","regressions":[]}}]}}。每个案例的 reason 必填；顶层整体 reason 可省略，无需重复案例依据。
对于 explicit_requirements，逐项输出 requirement_checks:[{{"source":"反馈 source ID","fulfilled":true,"evidence":"候选落实要求的具体位置或未落实的具体证据"}}]。人类明确要求高于一般评分偏好；不得因不喜欢该要求本身而判退步。检查实现是否满足要求，实际副作用仍如实记录。
regressions 只列会实质影响使用的新增事实、引用或核心覆盖退步；没有则空列表。最终说明比较是否完成及结果位置。
'''


def compare(store,runtime,job,comparisons,folder,backend,*,submission_contract=None):
    """Pairwise Evaluator over trial drafts; returns the saved comparison."""
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'input.json').write_text(dump(comparisons),encoding='utf-8')
    staged=stage_job(store,job,'evaluator',mode='pairwise')
    if backend=='briefloop-native':
        # A frozen packet and a validated submit instead of a written file.
        from .native_roles import comparison_packet
        comparison_packet(store,comparisons,folder,submission_contract=submission_contract)
        staged={**staged,'native_packet':{'role':'evaluator','evaluation_mode':'pairwise',
                 **({'comparison_contract':submission_contract} if submission_contract is not None else {})}}
    elif submission_contract is not None:
        raise ValueError('当前宿主尚不支持比较扩展契约的提交前校验')
    prompt=comparison_prompt(store,folder,backend)
    # This pairwise mode is BriefLoop's feedback policy, not an extra paper role.
    # Trial drafts skip single evaluation; this comparison is their sole judge.
    runtime.execute(staged,prompt,folder)
    return json.loads((folder/'comparison.json').read_text(encoding='utf-8'))


TRIAGE_RULES='''逐处判断用户改动的性质（category），只看这一处改动本身：
- taste：措辞、结构、详略、语气、排序、标题等写法偏好；原稿内容本身没有错。
- fact_correction：原稿的数字、事实、主体、日期、来源说法或统计口径有误，用户改正了它；来源在本轮之后正常更新导致的改动也算这一类（不是写作经验）。
- reader_specific：只因为这位读者的用途、身份或忌讳才这样改，换一位读者未必要改。
能对照来源判断时先看来源：改动后的内容与来源一致、原稿与来源不一致，是 fact_correction。
confident：你有把握时为 true；只看改动前后文字无法判断原因、多种解释都合理时为 false，不要猜成有把握。
reason 写一句具体依据（例如对照了哪份来源、哪个数字）。不改稿、不补搜、不评价改动好坏。
item 带 learned_edits 时，那是这份报告所用技能此前学过的改动：逐处判断本次改动是否又在改同一类问题（技能没能避免，用户只好再改一次），在 repeats 列出对应 learned_edits 的 id；不是重复则 []。'''


def triage_prompt(store,folder,backend='codex'):
    material=f"读取 {folder/'input.json'}：每个 item 是一次用户改稿，edits 是拆好的单处改动（before 改前、after 改后、heading 所在章节），reader 是这份报告的读者与目的。来源目录 {store.root/'sources'}。"
    output='写 triage.json：'
    context=EVALUATOR_CONTEXT
    if backend=='briefloop-native':
        context='本次分类只能读取固定任务包：路径一律相对任务包根目录。\n'
        material='读取 input.json：每个 item 是一次用户改稿，edits 是拆好的单处改动（before 改前、after 改后、heading 所在章节），reader 是这份报告的读者与目的。来源原文在 sources/<来源ID>.txt。'
        output='调用 submit_triage 提交（运行器当场校验，未通过时按错误修正后重交，不要把 JSON 写进回复正文）：'
    return context+f'''
本轮是改动分类模式。{material}
本轮没有任何用户在旁可问：不要调用宿主的提问或等待授权的工具。
{TRIAGE_RULES}
{output}{{"edits":[{{"feedback_id":"...","edit_id":"...","category":"taste|fact_correction|reader_specific","confident":true,"reason":"具体依据","repeats":[]}}]}}。每处改动须且只能给一条。
'''


def triage(store,runtime,job,folder,backend):
    """Split and classify this batch's unsplit revisions; one Evaluator turn per batch."""
    from . import revision_edits
    items=revision_edits.pending(store,json.loads(job['payload'])['feedback_ids'])
    if not items:return None
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'input.json').write_text(dump(items),encoding='utf-8')
    saved=folder/'triage.json'
    if not saved.exists():
        staged=stage_job(store,job,'evaluator',mode='triage')
        if backend=='briefloop-native':
            from .native_roles import triage_packet
            triage_packet(store,items,folder)
            staged={**staged,'native_packet':{'role':'evaluator','evaluation_mode':'triage'}}
        runtime.execute(staged,triage_prompt(store,folder,backend),folder)
    result=json.loads(saved.read_text(encoding='utf-8'))
    revision_edits.record(store,items,result,job_id=job['id'])
    asked=[e for e in revision_edits.snapshot(store)['questions'] if e['feedback_id'] in {i['feedback_id'] for i in items}]
    if asked:
        from .notifications import post
        post(store,f"revision-questions:{job['id']}",'learning',f'{len(asked)} 处改动需要你确认原因',
             body='在反馈面板里选择每处改动是口味、事实纠错还是只针对这位读者；跳过的改动不进入学习。',target={'job_id':job['id']})
    return result


def _attempt_source_snapshot(store,job,result):
    """Read the saved attempt, never reconstruct its past from a live run."""
    if 'source_snapshot' in result:
        rows=result['source_snapshot']
        if not isinstance(rows,list):return None
        result=[];seen=set()
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'source_id','text_hash','original_hash'}:return None
            if not isinstance(row['source_id'],str) or not isinstance(row['text_hash'],str):return None
            if row['source_id'] in seen:return None
            if row['original_hash'] is not None and not isinstance(row['original_hash'],str):return None
            seen.add(row['source_id']);result.append(row)
        return sorted(result,key=lambda row:row['source_id'])
    # A legacy input can establish the initial source set. If acquired sources
    # appeared later, the full-set comparison below fails conservatively.
    path=store.root/'jobs'/job['id']/'input.json'
    if (not path.is_file() or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(store.root))
            or not path.resolve().is_relative_to(store.root.resolve())):return None
    try:value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return None
    if not isinstance(value,dict):return None
    rows=value.get('sources')
    if not isinstance(rows,list):return None
    from .media import source_files
    out=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):return None
        sid=row.get('source_id',row.get('id'));text_hash=row.get('hash')
        if not isinstance(sid,str) or not isinstance(text_hash,str) or sid in seen:return None
        if row.get('id',sid)!=sid:return None
        seen.add(sid)
        try:_,_,original=source_files(store,sid)
        except (ValueError,OSError):return None
        original_hash=row.get('original_hash',row.get('raw_sha256'))
        # An original path without a captured hash cannot prove the bytes the
        # old model saw. Inline-only sources can be matched by text hash.
        if (original or row.get('original_path')) and not isinstance(original_hash,str):return None
        out.append({'source_id':sid,'text_hash':text_hash,'original_hash':original_hash})
    return sorted(out,key=lambda row:row['source_id'])


def _baseline_for_attempt(store, case, learning_payload):
    """Reuse only the version returned by a matching completed attempt."""
    if case['skill_id']!=learning_payload.get('skill_id'):return None
    try:conditions=case.get('learning_conditions') or _conditions(store,case,learning_payload)
    except (ValueError,KeyError):return None
    from .review_learning import source_snapshot
    try:expected=source_snapshot(store,case['id'],source_ids=case.get('learning_source_ids',store.source_ids(case['id'])))
    except (ValueError,OSError):return None
    for job in store.rows("SELECT * FROM jobs WHERE kind='generate' AND status='complete' ORDER BY rowid DESC"):
        payload=json.loads(job['payload'])
        if payload.get('run_id')!=case['id']:continue
        if payload.get('runtime')!=learning_payload.get('runtime'):continue
        if payload.get('agent_backend','codex')!=learning_payload.get('agent_backend','codex'):continue
        if payload.get('role_models')!=learning_payload.get('role_models'):continue
        # Skill overrides do not establish a like-for-like baseline.
        if payload.get('skill_override') is not None:continue
        result=json.loads(job['result'] or '{}');vid=result.get('version_id')
        if result.get('learning_conditions')!=conditions:continue
        input_path=store.root/'jobs'/job['id']/'input.json'
        try:actual_requirements=json.loads(input_path.read_text(encoding='utf-8'))['requirements'];actual=actual_requirements['workflow_snapshot']
        except (OSError,ValueError,KeyError,TypeError):continue
        try:valid=validated_workflow(actual)
        except ValueError:continue
        if actual_requirements!=conditions['requirements'] or valid!=conditions['requirements']['workflow_snapshot']:continue
        if _attempt_source_snapshot(store,job,result)!=expected:continue
        if not vid or not store.generated_by(vid,job['id']):continue
        try:brief=store.one('briefs',vid)
        except ValueError:continue
        if brief['run_id']==case['id'] and brief['author']=='agent':return brief
    return None


def learn(store,runtime,job):
    payload=json.loads(job['payload'])
    from .learning_budget import verify
    try:verify(payload.get('authorization'))
    except LearningAuthorizationRequired as exc:
        # Pause instead of failing: the batch keeps its feedback and trials and
        # runs once the user confirms the bound (#727 review F3).
        raise InterruptedError(str(exc)) from None
    root=store.root/'jobs'/job['id'];root.mkdir(exist_ok=True)
    study=root/'study';context=root/'context.json'
    from .readers import scope_meta
    reader_id=payload.get('reader_id');study_key=scope_meta('last_study',reader_id)
    if not context.exists():
        from .backends import validate_backend
        triage(store,runtime,job,root/'triage',validate_backend(payload.get('agent_backend','codex')))
        feedback,cases=_experience(store,job)
        from wikiskill.product import write
        write(context,{'feedback':feedback,'cases':cases,'previous_study':store.meta(study_key)},immutable=True)
    ctx=json.loads(context.read_text(encoding='utf-8'))
    if not ctx['feedback']:
        # Every edit was a fact correction or is waiting for the user's answer:
        # nothing becomes writing feedback, so no study and no trial generations.
        return {'study':None,'rounds':0,'history':[],'active_skill':store.meta('active_skill'),'skipped_cases':[],
                'comparison_skipped':'本批没有可作为写作经验的改动（事实纠错或待确认）'}
    current=store.one('skills',payload['skill_id']) if payload['skill_id'] else None
    skill_path=None
    if current:
        skill_path=root/'initial-skill.md';skill_path.write_text(current['content'],encoding='utf-8')
    previous=store.meta(study_key)
    if study.exists() and previous and previous not in (str(study),ctx.get('previous_study')):
        raise ValueError('已有后续学习记录，不能直接恢复旧学习任务；请基于当前 Wiki 发起新的反馈学习。旧进度保留。')
    feedback=ctx['feedback']
    retry_of=payload.get('retry_of_job_id')
    if retry_of and previous==str(store.root/'jobs'/retry_of/'study'):
        # A model-switch retry inherits the failed study's Wiki, including its
        # already imported feedback. Do not append that same Store batch twice.
        inherited={item.get('source') for item in feedback_loop.work(previous)['feedback']}
        batch=set(payload['feedback_ids'])
        feedback=[item for item in feedback if item.get('source') not in inherited.intersection(batch)]
    # One initial proposal plus one repair opportunity for explicit requirements.
    requirement_sources=[x['source'] for x in ctx['feedback'] if x.get('learning_intent')=='explicit_requirement' and x.get('origin')=='human']
    rounds=max(payload['k'],2) if requirement_sources else payload['k']
    if rounds>_budget(payload)['rounds_with_explicit_requirement']:
        raise LearningBudgetExhausted('学习轮数超过确认的上限，未开始；请重新确认后发起')
    # WikiSkill records the host that runs the learning children. A study that
    # already exists keeps the tag it was started with (resume must not change it).
    host=study_runtime(study) if (study/'config.json').exists() else __import__('briefloop.backends',fromlist=['validate_backend']).validate_backend(payload.get('agent_backend','codex'))
    feedback_loop.begin(study,feedback=feedback,skill=skill_path,rounds=rounds,previous=previous,runtime=host,**({'requirement_sources':requirement_sources} if requirement_sources else {}))
    # Only this worker writes the workspace's Wiki; one study at a time.
    store.set_meta(study_key,str(study))
    state=feedback_loop.work(study)
    cases,skipped=_eligible_cases(store,ctx['cases'])
    from wikiskill.product import write
    write(root/'eligibility.json',{'eligible':[case['id'] for case in cases],'skipped':skipped},immutable=True)
    if skipped and not store.rows("SELECT seq FROM events WHERE job_id=? AND kind='learning_cases_skipped'",(job['id'],)):
        store.event(job['id'],'learning_cases_skipped',{'cases':skipped})
    if not cases:
        state=feedback_loop.skip(study,reason='过滤后无有效来源，本轮不可比较；未调用模型、未启用候选',cases=skipped)
    cases=[_prepare_case(store,case,payload,root/'cases'/case['id']) for case in cases]
    while state['phase']!='complete':
        if runtime.cancelled.is_set():raise InterruptedError('学习已停止，进度保留')
        n=state['round'];store.event(job['id'],'learning_progress',{'round':n,'k':state['rounds'],'phase':state['phase']})
        if state['phase'] in ('maintainer','proposer'):
            _role(store,runtime,job,study,n,state['phase']);state=feedback_loop.work(study);continue
        if state['phase']!='validation':raise RuntimeError('未识别的学习步骤：'+state['phase'])
        candidate=state['candidate']
        if candidate['no_action']:
            state=feedback_loop.finish(study,pairs=[],reason=candidate['note']);continue
        text=(study/candidate['skill']['file']).read_text(encoding='utf-8')
        candidate_skill={'id':'candidate_'+content_hash(text)[:16],'content':text,'targets':dump(payload['targets'])}
        comparisons=[]
        for case in cases:
            case_id=case['id'];case_dir=root/f'round-{n}'/case_id
            baseline=_baseline_for_attempt(store,case,payload)
            if baseline is None:
                baseline=_generate_trial(store,{**job,'_runtime':runtime},case,current,case_dir/'baseline','baseline')
            proposed=_generate_trial(store,{**job,'_runtime':runtime},case,candidate_skill,case_dir/'candidate','candidate')
            comparisons.append({'case_id':case_id,'requirements':json.loads(case['requirements']),
                'conditions':case['learning_conditions'],'evaluation_method':__import__('briefloop.document_workflows',fromlist=['workflow_context']).workflow_context(json.loads(case['requirements'])['workflow_snapshot'],'evaluator'),'source_ids':json.loads(case['source_ids']),'comparison_scope':'固定来源的离线阅读与写作；不评估联网检索收益或联网事实核查质量','feedback_preferences':[json.loads(x['text']).get('comment') for x in ctx['feedback'] if json.loads(x['text']).get('kind')=='user_comment'],'explicit_requirements':[{'source':x['source'],'text':json.loads(x['text']).get('comment','')} for x in state['feedback'] if x.get('source') in state['explicit_requirement_sources'] and x.get('learning_intent')=='explicit_requirement' and x.get('origin')=='human'],'baseline':baseline,'candidate':proposed})
        folder=root/f'round-{n}'/'comparison'
        from .backends import validate_backend as _validate
        result=compare(store,runtime,job,comparisons,folder,_validate(payload.get('agent_backend','codex')))
        if {p['case_id'] for p in result['pairs']}!={x['case_id'] for x in comparisons}:raise ValueError('比较案例不完整')
        state=feedback_loop.finish(study,pairs=result['pairs'],reason=result.get('reason',''),evidence_file=folder/'comparison.json')
    apply_accepted(store,job,study,state)
    _sync_wiki(store,study,reader_id)
    return {'study':str(study),'rounds':len(state['history']),'history':state['history'],'active_skill':store.meta('active_skill'),'skipped_cases':skipped,'comparison_skipped':state.get('comparison_skipped')}


def apply_accepted(store,job,study,state):
    """Atomic, replayable deployment; a user rollback is never overwritten on resume."""
    accepted=[x for x in state['history'] if x['accepted']]
    if not accepted:return
    decision=accepted[-1];payload=json.loads(job['payload'])
    text=(Path(study)/decision['skill']['file']).read_text(encoding='utf-8')
    # A retained version includes its role binding. Keep legacy rows immutable;
    # target order and duplicate roles do not change the effective binding.
    targets=sorted(set(payload['targets']))
    sid='skill_'+content_hash(dump({'content':text,'targets':targets}))[:16]
    from .revision_edits import learned_ids
    learned=learned_ids(store,payload.get('feedback_ids',[]))
    with store.tx() as c:
        if c.execute("SELECT seq FROM events WHERE job_id=? AND kind='adoption_processed'",(job['id'],)).fetchone():return
        c.execute('INSERT OR IGNORE INTO skills VALUES(?,?,?,?,?,?)',(sid,payload['skill_id'],text,dump(targets),decision.get('reason',''),now()))
        reader_id=payload.get('reader_id')
        if reader_id:
            # A reader's skill binds only that reader's future reports.
            row=c.execute("SELECT value FROM meta WHERE key='reader_skills'").fetchone()
            bound=json.loads(row['value']) if row else {}
            row=c.execute("SELECT value FROM meta WHERE key='active_skill'").fetchone()
            current=bound.get(reader_id) or (json.loads(row['value']) if row else None)
            applied=current==payload['skill_id']
            if applied:c.execute("INSERT OR REPLACE INTO meta VALUES('reader_skills',?)",(dump({**bound,reader_id:sid}),))
        else:
            row=c.execute("SELECT value FROM meta WHERE key='active_skill'").fetchone()
            current=json.loads(row['value']) if row else None
            applied=current==payload['skill_id']
            if applied:c.execute("INSERT OR REPLACE INTO meta VALUES('active_skill',?)",(dump(sid),))
        if applied:
            from .skill_verification import register
            register(c,sid,reader_id,learned,job['id'])
        c.execute('INSERT INTO events(job_id,kind,data,created) VALUES(?,?,?,?)',(job['id'],'adoption_processed',dump({'skill_id':sid,'applied':applied,**({'reader_id':reader_id} if reader_id else {}),'reason':decision.get('reason','') if applied else '保留用户在比较期间的技能选择'}),now()))
