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
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for fid,figure in figures.items():
            path='assets/'+fid+'.png';text=text.replace('briefloop-figure:'+fid,path)
            archive.writestr(path,figure['image_bytes'])
        archive.writestr('report.md',text)
    return output.getvalue()
