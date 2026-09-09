from io import BytesIO
import json
import threading
import urllib.request
from PIL import Image
from pypdf import PdfWriter
from briefloop.server import make_server
from briefloop.sources import upload


def test_image_and_pdf_preview_use_real_http_without_model(tmp_path):
    server=make_server(tmp_path/'workspace',port=0,paused=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def get(path):
        with urllib.request.urlopen(base+path) as response:return response.read(),response.headers.get('Content-Type')
    try:
        png=BytesIO();Image.new('RGB',(90,60),'red').save(png,format='PNG')
        picture=upload(server.store,'picture.png',png.getvalue());assert picture['status']=='ready'
        detail=json.loads(get('/api/source?id='+picture['id'])[0])
        assert detail['attachment']['image_path'] and detail['image_url']
        raw,mime=get(detail['image_url']);assert mime=='image/png' and Image.open(BytesIO(raw)).size==(90,60)
        state=json.loads(get('/api/state')[0]);item=next(x for x in state['sources'] if x['id']==picture['id']);assert item['needs_visual']
        pdf=BytesIO();writer=PdfWriter();writer.add_blank_page(width=200,height=300);writer.add_blank_page(width=200,height=300);writer.write(pdf)
        source=upload(server.store,'scan.pdf',pdf.getvalue());assert source['status']=='ready'
        token=json.loads(get('/api/session')[0])['token']
        request=urllib.request.Request(base+'/api/source-pages',data=json.dumps({'source_id':source['id'],'pages':[2]}).encode(),headers={'X-BriefLoop-Token':token,'Content-Type':'application/json','Origin':base})
        result=json.load(urllib.request.urlopen(request));assert result['pages'][0]['page']==2
        raw,mime=get(result['pages'][0]['url']);assert mime=='image/png' and Image.open(BytesIO(raw)).height>0
        assert not server.worker.thread.is_alive()
    finally:
        server.shutdown();thread.join();server.harness.close();server.server_close();server.workspace_lock.close()
