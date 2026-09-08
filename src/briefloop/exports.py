"""Readable downloads from a saved version; never rewrite the original."""
from io import BytesIO
import json
import re
from markdown_it import MarkdownIt


def reader_markdown(store,brief):
    text=brief['markdown'];refs=json.loads(brief['detail']).get('citations',[]);used=[]
    def replace(match):
        sid=match.group(1).replace('\\','')
        if sid not in used:used.append(sid)
        return '['+str(used.index(sid)+1)+']'
    text=re.sub(r'\\?\[@(src\\?_[a-zA-Z0-9]+)\\?\]',replace,text)
    if used:
        text+='\n\n## 来源\n\n'
        for i,sid in enumerate(used,1):
            source=store.one('sources',sid)
            locators=list(dict.fromkeys(r.get('locator','') for r in refs if r['source_id']==sid and r.get('locator')))
            title=source['name']
            if source['url']:title=f'[{title}]({source["url"]})'
            text+=f'{i}. {title}'+(' · '+'；'.join(locators) if locators else '')+'\n'
    return text


def docx_bytes(markdown):
    from docx import Document
    doc=Document();tokens=MarkdownIt('commonmark').enable('table').parse(markdown)
    paragraph=None;table=None;row=None;cell=None;list_type=None
    for i,t in enumerate(tokens):
        if t.type=='heading_open':paragraph=doc.add_heading('',level=int(t.tag[1]))
        elif t.type=='bullet_list_open':list_type='List Bullet'
        elif t.type=='ordered_list_open':list_type='List Number'
        elif t.type in ('bullet_list_close','ordered_list_close'):list_type=None
        elif t.type=='paragraph_open' and table is None:paragraph=doc.add_paragraph(style=list_type)
        elif t.type=='table_open':
            first_row=[]
            for n in tokens[i+1:]:
                if n.type=='tr_close':break
                if n.type in ('th_open','td_open'):first_row.append(n)
            table=doc.add_table(rows=0,cols=max(1,len(first_row)));table.style='Table Grid'
        elif t.type=='tr_open' and table is not None:row=table.add_row();cell=-1
        elif t.type in ('th_open','td_open') and row is not None:
            cell+=1;paragraph=row.cells[cell].paragraphs[0]
        elif t.type=='table_close':table=None;row=None;cell=None;paragraph=None
        elif t.type=='inline':
            if paragraph is None:paragraph=doc.add_paragraph()
            bold=italic=False
            for child in t.children or []:
                if child.type=='strong_open':bold=True
                elif child.type=='strong_close':bold=False
                elif child.type=='em_open':italic=True
                elif child.type=='em_close':italic=False
                elif child.type in ('text','code_inline'):
                    run=paragraph.add_run(child.content);run.bold=bold;run.italic=italic
                elif child.type in ('softbreak','hardbreak'):paragraph.add_run('\n')
        elif t.type in ('fence','code_block'):doc.add_paragraph(t.content)
    buf=BytesIO();doc.save(buf);return buf.getvalue()
