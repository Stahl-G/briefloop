"""No inference: actual Reviewer-to-native-prompt construction and fixed pixels."""
import base64
from io import BytesIO
import json
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from briefloop.backends.opencode_server import OpencodeServerClient
from briefloop.document_model import markdown_document
from briefloop.evidence import create_span,create_claim,bind_claim
from briefloop.figures import register_figure
from briefloop.interactive_runtime import InteractiveRuntime
from briefloop.opencode_harness import OpencodeHarness
from briefloop.review import run_review,visual_input_files
from briefloop.sources import upload
from briefloop.store import Store,dump


def png(color):
    buffer=BytesIO();Image.new('RGB',(20,10),color).save(buffer,format='PNG');return buffer.getvalue()


class RecordingClient(OpencodeServerClient):
    """Keep production v1 prompt serialization; substitute only its HTTP request."""
    def __init__(self):self.requests=[];self.creations=[]
    def paths(self,directory):return {'worktree':str(directory)}
    def create_session(self,title,**kwargs):
        self.creations.append(kwargs);return {'id':'native-review'}
    def _request(self,method,path,body):self.requests.append((method,path,body))


class RecordedReviewHarness(OpencodeHarness):
    def _schedule(self,sid):
        # Mirror the production guard. Dispatching with an empty queue recurses
        # forever, because _dispatch re-schedules an idle session from its finally.
        if sid in self._busy or not any(m['status']=='queued' for m in self.chat.snapshot(sid)['messages']):return
        self._busy.add(sid);self._epoch[sid]=self._epoch.get(sid,0)+1
        self._dispatch(sid,self._epoch[sid])
    def _follow(self,sid,epoch,mid,admitted_at):
        folder=Path(self.chat.session(sid)['cwd'])
        index=json.loads((folder/'packet/index.json').read_text());target=json.loads((folder/'packet/target.json').read_text())
        # A transport acknowledgement must not invent a successful visual audit.
        reply={'fingerprint':index['fingerprint'],'version_id':index['version_id'],
               'status':'incomplete','summary':'Transport test only; no semantic reviewer ran.',
               'unchecked_items':[{'description':'No semantic visual review in this offline test','importance':'core'}],
               'assessment':{'brief_hash':target['brief_hash'],'status':'incomplete','summary':'Not reviewed','overall':'建议修改'}}
        self.chat.message(sid,dump(reply),role='assistant',status='completed',turn_id=mid)
        self.chat.patch_message(mid,status='completed')
        self.chat.update(sid,status='idle',turn_id=None)


def test_current_packet_visuals_reach_selected_model_and_history_does_not_suppress_them(tmp_path):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'agent_backend':'opencode','model':'example/current-vision-model','role_models':{}})
    text=store.add_source('Numbers','period,value\nA,10\nB,12\n')
    source_image=upload(store,'selected.png',png('red'));unrelated=upload(store,'not-selected.png',png('blue'))
    pdf=PdfWriter()
    for _ in range(3):pdf.add_blank_page(width=180,height=100)
    pdf_bytes=BytesIO();pdf.write(pdf_bytes);source_pdf=upload(store,'selected-page.pdf',pdf_bytes.getvalue())
    run=store.create_run({'title':'Visual packet','objective':'Explain operating mix'},[row['id'] for row in (text,source_image,source_pdf,unrelated)])
    plot=store.root/'plot.png';plot.write_bytes(png('green'))
    figure=register_figure(store,run['id'],plot,'Current trend',source_ids=[text['id']])
    brief=store.publish(run['id'],{'title':'Visual packet','editor_document':markdown_document('Operating mix.\n\n'+figure['markdown'])})
    spans=[create_span(store,{'source_id':source_image['id'],'locator':{'kind':'image'},'excerpt':'Operating mix.'}),
           create_span(store,{'source_id':source_pdf['id'],'locator':{'kind':'pdf','page':2},'excerpt':'Operating mix.'})]
    claim=create_claim(store,run['id'],{'kind':'fact','statement':'Operating mix.',
        'supports':[{'span_id':span['id'],'supports_quote':'Operating mix.'} for span in spans]})
    block=json.loads(brief['editor_document'])['content'][0]['attrs']['blockId'];bind_claim(store,brief['id'],claim['id'],block,'Operating mix.')
    prior={'unchecked':['Historical text-only model could not view the chart.']}
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',('old-review',brief['id'],None,'old','incomplete','{}',dump(prior),'2026','2026'))
    job=store.enqueue('review',{'version_id':brief['id']});folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    # This legacy task-pack figure must never leak into a restricted Review.
    (folder/'input.json').write_text(dump({'figures':[{'figure_id':'unrelated','absolute_image_path':str(store.root/unrelated['path'])}]}))
    client=RecordingClient();harness=RecordedReviewHarness(store,lambda _:client)
    runtime=InteractiveRuntime(store,backends={'opencode':harness})
    reviewed=run_review(store,runtime,job,brief['id'],folder)
    assert reviewed['status']=='incomplete'  # Pixel delivery is not a pass.
    body=client.requests[0][2]
    assert body['model']=={'providerID':'example','modelID':'current-vision-model'}
    files=[part for part in body['parts'] if part['type']=='file']
    assert len(files)==3
    attached={part['filename']:base64.b64decode(part['url'].split(',',1)[1]) for part in files}
    assert attached['figures_'+figure['figure_id']+'_image.png']==(store.root/figure['image_path']).read_bytes()
    assert Image.open(BytesIO(attached['sources_'+source_image['id']+'.visual.png'])).getpixel((0,0))==(255,0,0)
    assert 'sources_'+source_pdf['id']+'.page-2.png' in attached
    assert not any(unrelated['id'] in name or 'page-1' in name or 'page-3' in name for name in attached)
    index=json.loads((folder/'packet/index.json').read_text())
    indexed={row['id']:row for row in index['sources']}
    assert indexed[source_pdf['id']]['visual_files']==['sources/'+source_pdf['id']+'.page-2.png']
    assert not indexed[unrelated['id']].get('visual_files')
    history=json.loads((folder/'packet/history/reviews.json').read_text())
    assert history[0]['result']==prior  # The historical limitation stays history.
    delivered=store.rows("SELECT data FROM chat_events WHERE kind='message/delivered'")
    record=json.loads(delivered[0]['data']);assert len(record['visual_inputs'])==3
    assert all(item['delivery']=='attached' and item['sha256'] for item in record['visual_inputs'])
    assert 'data:image' not in delivered[0]['data']
    rules=client.creations[0]['permission']
    assert rules[0]=={'permission':'*','action':'deny','pattern':'*'}
    assert all(rule['permission'] in ('read','external_directory') for rule in rules if rule['action']=='allow')
    for rule in rules:
        if rule['permission']=='read' and rule['action']=='allow':assert (Path(rule['pattern']) if Path(rule['pattern']).is_absolute() else folder/rule['pattern']).resolve().is_relative_to(folder/'packet')
    assert len(visual_input_files(store,reviewed['id'],folder/'packet'))==3
    frozen=folder/'packet/figures'/figure['figure_id']/'image.png';frozen.write_bytes(png('black'))
    with pytest.raises(ValueError,match='核查包文件已变化'):
        harness._input({'text':'Review','source_ids':[], 'runtime':{'review_id':reviewed['id'],'review_root':str(folder/'packet')}},folder)
