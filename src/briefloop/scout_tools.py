"""Structural joining only; Analyst resolves semantic conflicts."""
from pathlib import Path
import json
import re
from .models import ScoutResult

# A parseable handoff locator: a line/page range, or a JSON-serialized evidence
# locator (the same shapes evidence.py reads back). Anything else is a structured
# error the agent can repair from instead of a silently unreadable citation.
_LOCATOR_RANGE=re.compile(r'(?i)^(line|page)\s+(\d+)(?:\s*-\s*(\d+))?$')


def _locator_problem(locator):
    text=locator.strip()
    match=_LOCATOR_RANGE.fullmatch(text)
    if match:
        start=int(match.group(2));end=int(match.group(3) or start)
        if start<1 or end<start:return '行号/页码必须从 1 开始且终点不小于起点'
        return None
    if text.startswith('{'):
        try:parsed=json.loads(text)
        except ValueError:return 'JSON 无法解析'
        if not isinstance(parsed,dict) or not isinstance(parsed.get('kind'),str) or not parsed['kind']:
            return 'JSON 需要是带 kind 字段的证据定位对象（如 pdf/page、xlsx/sheet/cells）'
        return None
    return '必须是 line 3、line 3-7、page 2 这样的行号/页码，或序列化为字符串的证据定位 JSON'


def join_scouts(store, paths, *, run_id=None, round_id=None, slots=None):
    """Structural merge only; no semantic adjudication.

    With a run/round/slot contract it also checks that each result belongs to an
    allocated slot and that every referenced source/claim is allowed for the run.
    """
    if round_id:
        from .research_plan import frozen
        plan = frozen(store, run_id) if run_id else None
        info = (plan or {}).get('rounds', {}).get(round_id)
        if not info:
            raise ValueError('Scout 轮次不属于本任务')
        if slots is None and info.get('tasks'):
            slots = [str(Path(task['directory']) / 'result.json') for task in info['tasks']]
    allowed_slots={str(Path(slot).resolve()) for slot in slots} if slots is not None else None
    results=[];seen=set();gaps=[];summaries=[];notes=[]
    for value in paths:
        path=Path(value).resolve()
        if not path.is_relative_to(store.root):raise ValueError('Scout output must be inside this workspace')
        if allowed_slots is not None and str(path) not in allowed_slots:
            raise ValueError('Scout 输出文件不在本任务分配的槽位内：'+path.name)
        result=ScoutResult.model_validate(json.loads(path.read_text(encoding='utf-8-sig')))
        allowed=set(store.source_ids(run_id)) if run_id else None
        for item in result.sources:
            store.one('sources',item.source_id)
            if allowed is not None and item.source_id not in allowed:
                raise ValueError('Scout 来源未登记到本轮报告：'+item.source_id)
            for claim_id in item.claim_ids:
                from .evidence import record
                claim=record(store,'claims',claim_id)
                if run_id and claim['run_id']!=run_id:raise ValueError('Scout 引用了不属于本轮的主张')
                if claim['data'].get('claim_role','report_statement')!='source_statement':
                    raise ValueError('Scout 的 claim_ids 只能引用来源陈述')
            data=item.model_dump();key=json.dumps(data,sort_keys=True,ensure_ascii=False)
            if key not in seen:results.append(data);seen.add(key)
        for gap in result.gaps:
            if gap not in gaps:gaps.append(gap)
        if result.search_summary:summaries.append(result.search_summary)
        notes.extend(result.retrieval_notes)
    merged=ScoutResult(sources=sorted(results,key=lambda r:(r['source_id'],r['locator'],r['excerpt'])),gaps=gaps,
                       search_summary='\n'.join(summaries),retrieval_notes=notes)
    return merged.model_dump()


class HandoffError(ValueError):
    """All schema violations of one agent-written handoff, structured for self-repair."""
    def __init__(self,errors):
        super().__init__('handoff.json 不符合交接契约（共 '+str(len(errors))+' 项）：'
                         +'；'.join(error['message'] for error in errors[:3]))
        self.errors=errors


def _handoff_texts(handoff,key,errors):
    value=handoff.get(key,[])
    if not isinstance(value,list) or any(not isinstance(item,str) or not item.strip() for item in value):
        errors.append({'path':key,'code':'not_text_list','message':key+' 必须是非空文本数组'})
        return []
    return value


def check_handoff(store,run_id,handoff):
    """Schema-check one round handoff: the agent writes it, Python only validates.

    A cited learning must carry a source registered on this run plus a parseable
    locator; a bare URL is rejected with a structured error the agent can repair
    from. An uncited learning stays valid but is marked 待证 for the next round
    to verify or drop.
    """
    if not isinstance(handoff,dict):raise HandoffError([{'path':'','code':'not_object','message':'handoff 必须是 JSON 对象'}])
    errors=[];registered=set(store.source_ids(run_id))
    raw=handoff.get('learnings')
    if not isinstance(raw,list):
        errors.append({'path':'learnings','code':'not_list','message':'缺少 learnings 数组（没有结论时给空数组）'})
        raw=[]
    learnings=[]
    for position,item in enumerate(raw):
        where='learnings['+str(position)+']'
        summary=item.get('summary') if isinstance(item,dict) else None
        if not isinstance(summary,str) or not summary.strip():
            errors.append({'path':where,'code':'summary_missing','message':where+' 缺少非空 summary'});continue
        url=item.get('url');source_id=item.get('source_id');locator=item.get('locator')
        if (isinstance(url,str) and url.strip()) or (isinstance(source_id,str) and source_id.strip().lower().startswith(('http://','https://'))):
            errors.append({'path':where,'code':'bare_url','message':where+' 的引用是裸 URL；先用 add-url 登记取得 source_id，再以 source_id+locator 引用'});continue
        if source_id is None and locator is None:
            learnings.append({'summary':summary,'status':'待证'});continue
        if not isinstance(source_id,str) or not source_id.strip() or not isinstance(locator,str) or not locator.strip():
            errors.append({'path':where,'code':'citation_incomplete','message':where+' 引用必须同时给 source_id 与可解析 locator；无引用就整条按待证处理'});continue
        problem=_locator_problem(locator)
        if problem is not None:
            errors.append({'path':where+'.locator','code':'locator_unparseable','message':where+'.locator 不可解析：'+problem});continue
        if source_id not in registered:
            errors.append({'path':where+'.source_id','code':'unknown_source','message':where+' 引用的来源未登记到本任务：'+source_id});continue
        learnings.append({'summary':summary,'source_id':source_id,'locator':locator,'status':'已引用'})
    follow_ups=_handoff_texts(handoff,'follow_ups',errors)
    covered=_handoff_texts(handoff,'covered',errors)
    open_questions=_handoff_texts(handoff,'open_questions',errors)
    if not isinstance(handoff.get('budget',{}),dict):
        errors.append({'path':'budget','code':'not_object','message':'budget 若填写必须是剩余预算视图对象'})
    if errors:raise HandoffError(errors)
    return {'learnings':learnings,'follow_ups':follow_ups,'covered':covered,'open_questions':open_questions,
            'unverified':sum(1 for item in learnings if item['status']=='待证')}


def read_source(store,source_id,*,start_line=None,end_line=None,max_chars=None):
    """Optional bounded view; the persisted source and unbounded output stay intact."""
    text=store.source_text(source_id)
    from .media import source_attachment
    attachment=source_attachment(store,source_id)
    media_header=''
    if attachment['media_type'].startswith('image/') or attachment['media_type']=='application/pdf':
        media_header='[本地视觉来源附件；以下元信息不是 OCR 或图表识别结果]\n'+json.dumps(attachment,ensure_ascii=False)+'\n'
    if start_line is None and end_line is None and max_chars is None:return media_header+text
    for name,value in (('start-line',start_line),('end-line',end_line),('max-chars',max_chars)):
        if value is not None and (type(value) is not int or value<1):raise ValueError(name+' 必须为正整数')
    lines=text.splitlines();total=len(lines);start=start_line or 1;end=end_line or total
    if not total:return media_header+f'[来源 {source_id}：共 0 行，无可读正文]'
    if start>total:raise ValueError(f'来源仅有 {total} 行，start-line 超出范围')
    if end<start:raise ValueError('end-line 不能小于 start-line')
    end=min(end,total);selected=lines[start-1:end];shown=[];remaining=max_chars;partial=False
    for line in selected:
        if remaining is not None:
            if shown:remaining-=1
            if remaining<=0:break
            if len(line)>remaining:
                shown.append(line[:remaining]);partial=True;break
            remaining-=len(line)
        shown.append(line)
    actual_end=start+len(shown)-1
    omitted=start>1 or actual_end<total or partial
    state='已截取部分正文' if omitted else '完整正文'
    if partial:state+='，末行仅显示部分字符'
    header=f'[来源 {source_id}：共 {total} 行；显示第 {start}–{actual_end} 行；{state}。max-chars 仅限制正文字符，不含行号和说明。]'
    return media_header+header+'\n'+'\n'.join(f'{start+i}: {line}' for i,line in enumerate(shown))
