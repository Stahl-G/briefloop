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
