"""Reader-facing progress from public messages and actual agent/tool events.

Deliberately never exports reasoning items, raw commands, or tool outputs.
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import re
import time
from .store import dump


def role_label(role):
    text=str(role or '子任务')
    if any(key in text.lower() for key in ('evaluator','scorer','assessor')):
        if any(key in text.lower() for key in ('assessor','pairwise','比较')):return 'Evaluator · 比较'
        if any(key in text.lower() for key in ('scorer','single','评分')):return 'Evaluator · 评分'
        return 'Evaluator'
    for key,label in [('maintainer','Maintainer'),('proposer','Proposer'),('analyst','Analyst'),('scout','Scout')]:
        if key in text.lower():
            number=re.search(r'(\d+)$',text)
            return label+(' '+number.group(1) if number else '')
    return text[:60]


class ProgressTracker:
    def __init__(self,store,job_id,folder):
        self.store=store;self.job_id=job_id;self.folder=Path(folder)
        self.signature=None;self.offset=0;self.tail=b'';self.message='';self.workers={}
        rows=store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1",(job_id,))
        self.last=rows[0]['data'] if rows else None

    def update(self):
        paths=[self.folder/n for n in ('events.jsonl','agents.json','plan.json','draft.json','assessment.json')]+[
            self.folder/'evaluation'/'assessment.json',self.folder/'scorer'/'assessment.json']
        signature=tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None for p in paths)
        if signature==self.signature:return
        self.signature=signature
        log=paths[0]
        if log.exists():
            with log.open('rb') as f:
                f.seek(self.offset);chunk=f.read();self.offset=f.tell()
            lines=(self.tail+chunk).split(b'\n');self.tail=lines.pop()
            for line in lines:
                try:event=json.loads(line)
                except (ValueError,UnicodeError):continue
                item=event.get('item',{})
                if event.get('type')=='item.completed' and item.get('type')=='agent_message':
                    self.message=item.get('text','')[:700]
                if item.get('type')=='collab_tool_call':
                    for identity,status in item.get('agents_states',{}).items():
                        row=self.workers.setdefault(identity,{'id':identity,'role':'子任务','status':'running'})
                        value=status.get('status') if isinstance(status,dict) else str(status)
                        if value:row['status']=value
        if paths[1].exists():
            try:
                for agent in json.loads(paths[1].read_text()).get('agents',[]):
                    identity=agent.get('agent_id') or agent.get('id')
                    if not identity:continue
                    row=self.workers.setdefault(identity,{'id':identity})
                    row['role']=role_label(agent.get('role'))
                    if row.get('status') not in ('completed','errored','failed'):
                        row['status']=agent.get('status','running')
                    row['task']=str(agent.get('responsibility',''))[:240]
            except (ValueError,OSError):pass
        workers=list(self.workers.values())
        active=[w for w in workers if w.get('status') not in ('completed','done','closed','failed','errored')]
        stage='正在整理任务要求'
        if paths[2].exists():stage='正在分配研究任务'
        if workers and not active:stage='子任务结果已返回，正在整理与交接'
        if paths[3].exists():stage='正文已保存，正在准备评分'
        if any(path.exists() for path in paths[4:]):stage='评分已返回，正在保存结果'
        labels=' '.join(w.get('role','') for w in active)
        for key,label in [('Scout','Scout 正在读取与核对来源'),('Analyst','Analyst 正在撰写简报'),('Evaluator · 比较','Evaluator 正在比较新旧稿件'),('Evaluator · 评分','Evaluator 正在独立评分'),('Evaluator','Evaluator 正在核对任务与来源'),('Maintainer','Maintainer 正在整理经验'),('Proposer','Proposer 正在提出技能')]:
            if key in labels:stage=label
        value={'stage':stage,'message':self.message,'agents':workers,'last_activity':datetime.fromtimestamp(log.stat().st_mtime if log.exists() else time.time(),timezone.utc).isoformat(),'draft_ready':paths[3].exists()}
        encoded=dump(value)
        if encoded!=self.last:
            self.store.event(self.job_id,'runtime_progress',value);self.last=encoded

