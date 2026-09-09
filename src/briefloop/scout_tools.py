"""Structural joining only; Analyst resolves semantic conflicts."""
from pathlib import Path
import json
from .models import ScoutResult


def join_scouts(store, paths):
    results=[];seen=set();gaps=[]
    for value in paths:
        path=Path(value).resolve()
        if not path.is_relative_to(store.root):raise ValueError('Scout output must be inside this workspace')
        result=ScoutResult.model_validate(json.loads(path.read_text()))
        for item in result.sources:
            store.one('sources',item.source_id)
            data=item.model_dump();key=json.dumps(data,sort_keys=True,ensure_ascii=False)
            if key not in seen:results.append(data);seen.add(key)
        for gap in result.gaps:
            if gap not in gaps:gaps.append(gap)
    return ScoutResult(sources=sorted(results,key=lambda r:(r['source_id'],r['locator'],r['excerpt'])),gaps=gaps).model_dump()


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
