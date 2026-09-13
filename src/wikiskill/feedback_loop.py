"""Public feedback-first extension to the WikiSkill product journal.

Reuse product/native-agent requests for Maintainer and Proposer. The host supplies
real paired results; deterministic selection records the lightweight policy.
The original numeric task loop remains unchanged.
"""
from pathlib import Path
from . import product as p


def begin(root, *, feedback, skill=None, rounds=1, previous=None, runtime='codex'):
    import tempfile
    from collections import Counter
    root=Path(root).resolve();root.parent.mkdir(parents=True,exist_ok=True)
    feedback=list(feedback)
    if any(not isinstance(item.get('text'),str) or not item['text'].strip() for item in feedback):
        raise ValueError('Feedback must not be blank')
    manifest=root.with_name('.'+root.name+'.feedback-init.json')
    intent={'feedback':feedback,'rounds':rounds,'runtime':runtime,
            'skill_sha256':p.file_hash(skill) if skill else None}
    if manifest.exists():
        if p.read(manifest)!=intent:raise ValueError('Feedback initialization conditions changed; keep this study and start a new one')
    else:p.write(manifest,intent,immutable=True)
    if not (root/'config.json').exists():
        if root.exists() and any(root.iterdir()):raise ValueError('Incomplete unrecognized study; existing files were preserved')
        # Publish the complete base configuration atomically. An interrupted
        # staging directory is retained; it contains no executed model requests.
        staging=Path(tempfile.mkdtemp(prefix='.'+root.name+'-initializing-',dir=root.parent))
        p.start(staging,skill=skill,rounds=rounds,from_workspace=previous,agent_runtime=runtime)
        if root.exists():root.rmdir()  # Only the empty directory checked above.
        staging.rename(root)
    with p.locked(root):
        state=p._load(root)
        if state['config']['rounds']!=rounds:raise ValueError('Feedback rounds changed')
        if state.get('feedback_mode') and state['phase']!='baseline':
            p._wiki_view(root,state)
            return work(root)
        if state['requests'] or state['tasks']:raise ValueError('Existing study is not an unstarted feedback study')
        counts=Counter(p.digest({'text':row['text'],'source':row.get('source')}) for row in state['feedback'])
        wanted=Counter()
        for item in feedback:
            key=p.digest({'text':item['text'],'source':item.get('source')});wanted[key]+=1
            if counts[key]>=wanted[key]:continue
            fid='feedback-init-'+key[:24]+'-'+str(wanted[key])
            path=root/'feedback'/(fid+'.md');path.parent.mkdir(exist_ok=True)
            if path.exists() and path.read_text(encoding='utf-8')!=item['text']:raise ValueError('Initialization feedback artifact changed')
            path.write_text(item['text'],encoding='utf-8')
            value={'id':fid,'text':item['text'],'source':item.get('source'),'origin':'user_feedback',
                   'file':path.relative_to(root).as_posix(),'at':p.now()}
            state=p._event(root,state,'feedback',value,[path]);counts[key]+=1
        if not state.get('feedback_mode'):state=p._event(root,state,'feedback_mode',{})
        state=p._event(root,state,'phase',{'phase':'maintainer', **({'current_skill':None} if skill is None else {})})
        p._wiki_view(root,state)
    return work(root)


def skip(root, *, reason, cases):
    """An ineligible batch never creates a comparison or an adoption decision."""
    root=Path(root).resolve()
    with p.locked(root):
        state=p._load(root)
        if state['phase']!='complete':
            p._event(root,state,'phase',{'phase':'complete','comparison_skipped':{'reason':reason,'cases':cases}})
    return work(root)


def work(root):
    root=Path(root).resolve();s=p._load(root)
    if not s.get('feedback_mode'):raise ValueError('Not a feedback study')
    return {'phase':s['phase'],'round':s['round'],'rounds':s['config']['rounds'],
            'candidate':s['candidate'],'current_skill':s['current_skill'],
            'patterns':s['patterns'],'history':s['history'],'feedback':s['feedback'],
            'comparison_skipped':s.get('comparison_skipped')}


def finish(root, *, pairs, reason='', evidence_file=None):
    root=Path(root).resolve()
    with p.locked(root):
        s=p._load(root)
        if s['phase']!='validation' or not s['candidate']:raise ValueError('No candidate awaiting comparison')
        candidate=s['candidate'];no_action=candidate['no_action']
        if not no_action:
            if not pairs or len({x['case_id'] for x in pairs})!=len(pairs):raise ValueError('Provide distinct paired task results')
            if any(x.get('verdict') not in ('better','tie','worse') or not isinstance(x.get('regressions'),list) for x in pairs):
                raise ValueError('Each pair needs a verdict and explicit regressions')
        better=sum(x['verdict']=='better' for x in pairs)
        worse=sum(x['verdict']=='worse' for x in pairs)
        accepted=not no_action and better>worse and not any(x['regressions'] for x in pairs)
        files=[]
        if evidence_file:
            record,path=p._store_file(root,evidence_file,'comparison.json');files.append(path)
        else:record=None
        value={'round':s['round'],'accepted':accepted,'no_action':no_action,
               'verdict':'NO_ACTION' if no_action else 'ACCEPT' if accepted else 'REJECT',
               'skill':candidate['skill'] if accepted else s['current_skill'],
               'candidate_skill':candidate['skill'],'pairs':pairs,'reason':reason,
               'evidence':record,'incumbent_score':None,'candidate_score':None,'improvement':None,
               'policy':'lightweight_pairwise'}
        p._event(root,s,'feedback_gate',value,files)
    return work(root)
