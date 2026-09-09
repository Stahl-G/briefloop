"""Read workbook cells/drawing inventory and extract embedded pixels without editing it."""
from io import BytesIO
from pathlib import PurePosixPath,Path
import posixpath,zipfile,json,hashlib
import xml.etree.ElementTree as ET
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships','x':'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing','a':'http://schemas.openxmlformats.org/drawingml/2006/main','c':'http://schemas.openxmlformats.org/drawingml/2006/chart'}

def _archive(data):
    z=zipfile.ZipFile(BytesIO(data))
    if sum(i.file_size for i in z.infolist())>150_000_000:raise ValueError('工作簿展开后过大')
    return z

def _rels(z,part):
    rel=posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')
    if rel not in z.namelist():return {}
    out={}
    for n in ET.fromstring(z.read(rel)):
        if n.get('TargetMode')=='External':continue
        target=n.get('Target','');path=posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(part),target))
        if not path.startswith('xl/'):continue
        out[n.get('Id')]=path
    return out

def workbook_text(data):
    with _archive(data) as z:
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():shared=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rels=_rels(z,'xl/workbook.xml');lines=['工作簿单元格保存值；公式未重新计算。图表另有清单，不能把图表定义当作已读取像素。']
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS):
            part=rels.get(sheet.get('{'+NS['r']+'}id'));lines.append('\n## '+sheet.get('name',''))
            if not part:continue
            for row in ET.fromstring(z.read(part)).findall('s:sheetData/s:row',NS):
                cells=[]
                for c in row:
                    v=c.find('s:v',NS);text=v.text if v is not None else None
                    if c.get('t')=='s' and text is not None:text=shared[int(text)]
                    elif c.get('t')=='inlineStr':text=''.join(c.find('s:is',NS).itertext())
                    if text is None and c.find('s:f',NS) is not None:text='[公式没有保存值]'
                    if text is not None:cells.append(c.get('r','')+': '+text)
                if cells:lines.append(' | '.join(cells))
        return '\n'.join(lines)

def inspect_workbook(data):
    result=[]
    with _archive(data) as z:
        rels=_rels(z,'xl/workbook.xml')
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS):
            part=rels.get(sheet.get('{'+NS['r']+'}id'))
            if not part:continue
            links=_rels(z,part)
            for drawing in ET.fromstring(z.read(part)).findall('s:drawing',NS):
                dp=links.get(drawing.get('{'+NS['r']+'}id'))
                if not dp:continue
                dl=_rels(z,dp)
                for anchor in ET.fromstring(z.read(dp)):
                    pos=anchor.find('x:from',NS);origin={x.tag.rsplit('}',1)[-1]:x.text for x in pos} if pos is not None else {}
                    img=anchor.find('.//a:blip',NS);chart=anchor.find('.//c:chart',NS)
                    if img is not None:
                        target=dl.get(img.get('{'+NS['r']+'}embed'));kind='embedded_image';title='内嵌图片'
                    elif chart is not None:
                        target=dl.get(chart.get('{'+NS['r']+'}id'));kind='native_chart';title=''
                        if target:
                            xml=ET.fromstring(z.read(target));title=''.join(n.text or '' for n in xml.findall('.//c:title//a:t',NS))
                    else:continue
                    if target:result.append({'sheet':sheet.get('name'),'kind':kind,'part':target,'title':title,'anchor':origin})
    return result

def extract_workbook_figures(store,sid):
    from .media import source_files,safe_source_path
    source,_,original=source_files(store,sid)
    if original is None or original.suffix.lower()!='.xlsx':raise ValueError('需要已保存原件的 XLSX 来源')
    data=original.read_bytes();digest=hashlib.sha256(data).hexdigest();items=inspect_workbook(data)
    directory=safe_source_path(store,f'sources/media/{digest}/workbook',must_exist=False);directory.mkdir(parents=True,exist_ok=True)
    with _archive(data) as z:
        for i,item in enumerate(items):
            if item['kind']=='embedded_image':
                path=directory/(f'embedded-{i+1}'+PurePosixPath(item['part']).suffix);safe_source_path(store,path,must_exist=False);raw=z.read(item['part'])
                if path.exists() and path.read_bytes()!=raw:raise ValueError('已有图像与工作簿不一致')
                path.write_bytes(raw);item['image_path']=str(path)
            else:item['needs_render']=True
    return {'source_id':sid,'workbook_sha256':digest,'figures':items,'note':'内嵌图片为原字节提取；原生图表需使用可用工作簿渲染器，或复用经核对来自同版本工作簿的渲染图。不要把重新绘图称为原图。选图后用register-figure登记，并在正文放入返回的Markdown。'}
