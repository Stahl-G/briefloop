"""Deterministic finding locations, not semantic or factual verification."""
from .document_model import citation_label_text, document_markdown, markdown_document, source_ids
from .evidence import blocks


def _passages(document):
    numbers={sid:str(i) for i,sid in enumerate(source_ids(document),1)}
    def inline(node,mode):
        kind=node.get('type');attrs=node.get('attrs',{})
        def citation(sid):
            return '['+('@'+sid if mode=='stored' else sid if mode=='packet' else numbers[sid])+']'
        if kind=='citation':return citation(attrs['sourceId'])
        if kind=='text':
            text=node.get('text','')
            if mode=='packet':return text  # packet_views._inline preserves link labels.
            for mark in node.get('marks',[]):
                href=mark.get('attrs',{}).get('href','')
                if href.startswith('#source-'):
                    return citation_label_text(text)+citation(href[8:])
            return text
        if kind=='hardBreak':return ' '
        if kind=='image':
            text=attrs.get('caption') or attrs.get('alt') or attrs.get('src','')
            return '[图：'+text+']' if mode=='packet' else text
        return ''.join(inline(child,mode) for child in node.get('content',[]))
    def walk(node,parents):
        identity=node.get('attrs',{}).get('blockId')
        scope=parents|({identity} if identity else set())
        if node.get('type') in ('paragraph','heading','codeBlock','image'):
            for mode in ('stored','packet','reader'):
                yield scope,' '.join(inline(node,mode).split())
            yield scope,' '.join(document_markdown({'type':'doc','content':[node]}).split())
            return
        # Containers are not passages: joining cells or paragraphs could invent
        # a quotation that no reader sees as continuous text.
        for child in node.get('content',[]):yield from walk(child,scope)
    return list(walk(document,set()))


def unlocated_quotes(document,findings,*,reader_preview=''):
    """Indices of new findings whose report_quote is not continuous body text."""
    passages=_passages(document);known=set(blocks(document))
    preview=None;missing=[]
    for index,finding in enumerate(findings):
        value=finding.model_dump() if hasattr(finding,'model_dump') else finding
        if value.get('response_to'):continue  # A corrected historical quote may be gone.
        selected=set(value.get('block_ids') or [])
        if selected-known:raise ValueError(f'findings[{index}].block_ids 引用了本版不存在的正文块')
        quote=' '.join(str(value.get('report_quote') or '').split())
        if not quote:continue  # Missing-content findings need not invent a quotation.
        if any(quote in text for scope,text in passages if not selected or scope&selected):continue
        if not selected and reader_preview:
            if preview is None:preview=_passages(markdown_document(reader_preview))
            if any(quote in text for _,text in preview):continue
        missing.append(index)
    return missing


def validate_findings(document,findings,*,reader_preview=''):
    """Check only new, supplied anchors; omitted text and history stay valid."""
    missing=unlocated_quotes(document,findings,reader_preview=reader_preview)
    if missing:
        raise ValueError(f'findings[{missing[0]}].report_quote 无法在本版指定正文位置连续定位；'
                         '请复制实际原文，不拼接省略片段；缺失内容可省略 report_quote 并说明要求')


def drop_unlocatable(document,findings,*,reader_preview=''):
    """Last resort after a repair turn: keep each finding, drop only the anchors
    (unknown block_ids, non-continuous quotes) that cannot be located."""
    known=set(blocks(document));changed=set()
    for index,finding in enumerate(findings):
        if finding.get('response_to'):continue
        ids=finding.get('block_ids') or []
        if any(i not in known for i in ids):
            finding['block_ids']=[i for i in ids if i in known];changed.add(index)
    for index in unlocated_quotes(document,findings,reader_preview=reader_preview):
        findings[index]['report_quote']='';changed.add(index)
    return sorted(changed)
