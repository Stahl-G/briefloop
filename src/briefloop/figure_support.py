"""Bind report image markers to immutable, source-backed workspace figure assets."""
import json,re
from io import BytesIO
from zipfile import ZipFile,ZIP_DEFLATED


def validate_figures(store,run_id,markdown):
    from .figures import figure_ids,read_figure
    allowed=set(store.source_ids(run_id))
    refs=set(json.loads(store.one('runs',run_id)['requirements']).get('reference_source_ids',[]))
    figures=[]
    for fid in figure_ids(markdown):
        figure=read_figure(store,fid)
        if not set(figure['source_ids']).issubset(allowed-refs):raise ValueError('图表的数据来源未登记到本轮报告')
        figures.append(figure)
    return figures


def export_figures(store,brief):
    result={}
    for figure in validate_figures(store,brief['run_id'],brief['markdown']):
        result[figure['figure_id']]={'image_bytes':(store.root/figure['image_path']).read_bytes(),
            'title':figure['title'],'caption':figure['caption'],
            'source_labels':[store.one('sources',sid)['name'] for sid in figure['source_ids']]}
    return result


def markdown_bundle(store,brief):
    from .exports import reader_markdown
    figures=export_figures(store,brief);text=reader_markdown(store,brief)
    explicit_captions={}
    if brief.get('editor_document'):
        document=json.loads(brief['editor_document']) if isinstance(brief['editor_document'],str) else brief['editor_document']
        def collect(node):
            if node.get('type')=='image':
                attrs=node.get('attrs',{});fid=attrs.get('src','').removeprefix('briefloop-figure:')
                explicit_captions.setdefault(fid,[]).append('caption' in attrs)
            for child in node.get('content',[]):collect(child)
        collect(document)
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for fid,figure in figures.items():
            path='assets/'+fid+'.png'
            # Append the registered caption and provenance to each image, not
            # just to a body-citation bibliography (figures may be sole users).
            def plain(value):
                value=str(value).replace('\n',' ').replace('\r',' ')
                return re.sub(r'([\\`*_{}\[\]<>#!|])',r'\\\1',value)
            caption='\n\n'+plain(figure['caption']) if figure['caption'] else ''
            notes=''
            if figure['source_labels']:
                notes+='\n\n来源：'+'；'.join(plain(v) for v in figure['source_labels'])
            marker=re.compile(r'(!\[(?:\\.|[^\]\\])*\]\()<?'+re.escape('briefloop-figure:'+fid)+r'>?(\s*(?:"[^"\n]*")?\))')
            occurrences=iter(explicit_captions.get(fid,[]))
            def replace(m):
                # Rich projection already includes that node's caption (even an
                # explicit empty caption must not revive the registered one).
                extra='' if next(occurrences,False) else caption
                return m[1]+path+m[2]+extra+notes
            text=marker.sub(replace,text)
            archive.writestr(path,figure['image_bytes'])
        archive.writestr('report.md',text)
    return output.getvalue()
