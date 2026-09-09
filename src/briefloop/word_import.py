"""Explicit Word revision import bound to a selected report version."""
from io import BytesIO
from pathlib import Path
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import json
import re
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from .document_model import brief_document, normalize_document, document_markdown, source_ids
from .store import Conflict


def import_revision(store,base_version,name,data,*,accept_unaligned=False,source_id=None):
    from .sources import upload
    from .figures import read_figure,register_figure
    from .media import source_files
    base=store.one('briefs',base_version)
    latest=store.rows('SELECT id FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(base['run_id'],))[0]['id']
    if latest!=base_version:raise Conflict('基础版本已有更新，请选择当前版本导入')
    if source_id:
        source,_,original=source_files(store,source_id)
        if original is None or original.suffix!='.docx':raise ValueError('修订原件不是 DOCX')
        data=original.read_bytes()
    else:
        if Path(name).suffix.lower()!='.docx':raise ValueError('请导入 DOCX 修订稿')
        source=upload(store,name,data)
    if source['status']!='ready':raise ValueError('修订原件无法读取，失败记录已保留')
    provenance=store.root/'sources'/(source['id']+'.provenance.json')
    if provenance.is_file():
        metadata=json.loads(provenance.read_text())
        metadata['revision_for_runs']=list(dict.fromkeys(metadata.get('revision_for_runs',[])+[base['run_id']]))
        provenance.write_text(json.dumps(metadata,ensure_ascii=False,sort_keys=True))
    doc=Document(BytesIO(data));original=brief_document(base)
    refs=source_ids(original);headings={}
    for node in original.get('content',[]):
        if node['type']=='heading':headings[''.join(c.get('text','') for c in node.get('content',[]))]=node.get('attrs',{})
    known_images={}
    for fid in json.loads(base['detail']).get('figures',[]):
        figure=read_figure(store,fid)
        known_images[hashlib.sha256((store.root/figure['image_path']).read_bytes()).hexdigest()]=figure
    generated=[];notes=[];pending_images={};unsupported=[]
    def text_nodes(text,marks):
        out=[]
        for part in re.split(r'(\[\d+\])',text):
            if not part:continue
            match=re.fullmatch(r'\[(\d+)\]',part)
            if match and 1<=int(match[1])<=len(refs):out.append({'type':'citation','attrs':{'sourceId':refs[int(match[1])-1]}})
            else:out.append({'type':'text','text':part,**({'marks':deepcopy(marks)} if marks else {})})
        return out
    def paragraph(element):
        p=Paragraph(element,doc);text=p.text;attrs={};kind='paragraph'
        if text in headings:kind='heading';attrs=deepcopy(headings[text])
        elif p.style and p.style.name.startswith('Heading '):
            kind='heading';attrs={'level':int(p.style.name.rsplit(' ',1)[-1])}
        elif re.match(r'^[一二三四五六七八九十]+[、．.]',text):kind='heading';attrs={'level':2}
        alignment={0:'left',1:'center',2:'right',3:'justify'}.get(p.alignment)
        if alignment:attrs['textAlign']=alignment
        node={'type':kind,'attrs':attrs,'content':[]};images=[]
        for child in element:
            if child.tag not in (qn('w:r'),qn('w:hyperlink')):continue
            runs=[child] if child.tag==qn('w:r') else list(child.iter(qn('w:r')))
            link=None
            if child.tag==qn('w:hyperlink'):
                rel=doc.part.rels.get(child.get(qn('r:id')))
                if rel and rel.is_external and str(rel.target_ref).startswith(('http://','https://','mailto:')):link=str(rel.target_ref)
            for run in runs:
                marks=[];properties=run.find(qn('w:rPr'))
                if properties is not None:
                    for tag,mark in [('b','bold'),('i','italic'),('u','underline'),('strike','strike')]:
                        item=properties.find(qn('w:'+tag))
                        if item is not None and item.get(qn('w:val')) not in ('0','false','none'):marks.append({'type':mark})
                    color=properties.find(qn('w:color'))
                    if color is not None and re.fullmatch(r'[A-Fa-f0-9]{6}',color.get(qn('w:val'),'')):
                        marks.append({'type':'textStyle','attrs':{'color':'#'+color.get(qn('w:val'))}})
                if link:marks.append({'type':'link','attrs':{'href':link}})
                for item in run:
                    if item.tag==qn('w:t'):node['content'].extend(text_nodes(item.text or '',marks))
                    elif item.tag in (qn('w:br'),qn('w:cr')):
                        if item.get(qn('w:type'))!='page':node['content'].append({'type':'hardBreak'})
                    elif item.tag==qn('w:drawing'):
                        blips=item.xpath('.//a:blip')
                        if not blips:unsupported.append('原件含无法直接导入的原生图表/绘图对象，请先转为登记图片，原件已保留。')
                        for blip in blips:
                            rel=doc.part.rels.get(blip.get(qn('r:embed')))
                            if not rel or rel.is_external:raise ValueError('修订图片缺少内嵌资源')
                            blob=rel.target_part.blob;digest=hashlib.sha256(blob).hexdigest();figure=known_images.get(digest)
                            if figure is None:
                                suffix=Path(str(rel.target_part.partname)).suffix
                                path=store.root/'sources'/(source['id']+'-image-'+digest[:10]+suffix);path.write_bytes(blob)
                                figure={'figure_id':'fig_pending_'+digest[:16],'title':'用户修订图片'}
                                pending_images[figure['figure_id']]=path
                                known_images[digest]=figure;notes.append('用户修订引入新图像，需对照其原始数据核查。')
                            attrs_image={'src':'briefloop-figure:'+figure['figure_id'],'alt':figure['title']}
                            extent=item.xpath('.//wp:extent')
                            if extent:
                                attrs_image['width']=max(1,round(int(extent[0].get('cx'))/9525))
                            images.append({'type':'image','attrs':attrs_image})
        return ([node] if node['content'] else [])+images
    def table(element):
        rows=[];above={}
        for row_index,tr in enumerate(element.findall(qn('w:tr'))):
            cells=[];column=0
            header=tr.find(qn('w:trPr')+'/'+qn('w:tblHeader')) is not None
            for tc in tr.findall(qn('w:tc')):
                props=tc.find(qn('w:tcPr'));span=props.find(qn('w:gridSpan')) if props is not None else None
                cs=int(span.get(qn('w:val'))) if span is not None else 1
                merge=props.find(qn('w:vMerge')) if props is not None else None
                if merge is not None and merge.get(qn('w:val'),'continue')=='continue':
                    if column not in above:raise ValueError('合并单元格无法可靠对齐，原件已保留')
                    origin=above[column]
                    origin['attrs']['rowspan']=origin['attrs'].get('rowspan',1)+1
                    column+=cs;continue
                value={'type':'tableHeader' if header else 'tableCell','attrs':{'colspan':cs},'content':[]}
                shade=props.find(qn('w:shd')) if props is not None else None
                if shade is not None and re.fullmatch(r'[A-Fa-f0-9]{6}',shade.get(qn('w:fill'),'')):
                    value['attrs']['backgroundColor']='#'+shade.get(qn('w:fill'))
                for part in tc:
                    if part.tag==qn('w:p'):value['content'].extend(paragraph(part))
                    elif part.tag==qn('w:tbl'):unsupported.append('原件含嵌套表格，需先对齐为普通表格；原件已保留。')
                if not value['content']:value['content']=[{'type':'paragraph'}]
                if merge is not None:above[column]=value
                else:above.pop(column,None)
                cells.append(value);column+=cs
            rows.append({'type':'tableRow','content':cells})
        return {'type':'table','content':rows}
    blocks=list(doc.element.body);started=not headings;matched=[]
    for element in blocks:
        if element.tag==qn('w:p'):
            p=Paragraph(element,doc)
            if p.text in headings:started=True;matched.append(p.text)
            if not started:continue
            if p.text=='来源' and refs:break
            if p.style and p.style.name=='Caption' and generated and generated[-1]['type']=='image':
                lines=p.text.splitlines();image=generated[-1]
                if lines and lines[0]==image['attrs'].get('alt'):lines=lines[1:]
                image['attrs']['caption']='\n'.join(x for x in lines if not x.startswith('来源：'))
                continue
            generated.extend(paragraph(element))
        elif element.tag==qn('w:tbl') and started:generated.append(table(element))
    document=normalize_document({'type':'doc','content':generated})
    projection=document_markdown(document)
    aligned=(not headings and SequenceMatcher(None,base['markdown'],projection).ratio()>.2 or
             headings and matched==list(headings))
    if unsupported or not aligned and not accept_unaligned:
        return {'status':'needs_alignment','source_id':source['id'],'base_version':base_version,
                'message':'；'.join(dict.fromkeys(unsupported)) if unsupported else '章节无法可靠对齐，修订原件已保存，尚未生成学习反馈','extracted_markdown':projection,'notes':notes}
    if not projection.strip():raise ValueError('修订稿没有可导入正文，原件已保留')
    if pending_images:
        store.attach_source(base['run_id'],source['id'])
        replacements={}
        for pending_id,path in pending_images.items():
            replacements[pending_id]=register_figure(store,base['run_id'],path,'用户修订图片','',[source['id']])['figure_id']
        def replace_images(node):
            if node['type']=='image':
                fid=node['attrs']['src'].split(':',1)[1]
                if fid in replacements:node['attrs']['src']='briefloop-figure:'+replacements[fid]
            for child in node.get('content',[]):replace_images(child)
        replace_images(document)
    revised=store.revise(base_version,editor_document=document)
    store.event(None,'word_revision_imported',{'source_id':source['id'],'base_version':base_version,'version_id':revised['id'],'alignment_confirmed':bool(accept_unaligned),'notes':notes})
    return {'status':'imported','source_id':source['id'],'version':revised,'notes':notes}
