"""Native image blocks and handoff plumbing only; no model/vision-quality claim."""
import base64
import json
from pathlib import Path
from queue import Queue
import time
import pytest
from briefloop.harness import HarnessManager
from briefloop.interactive_runtime import InteractiveRuntime
from briefloop.runtime import generation_prompt,assessment_prompt,stage_job
from briefloop.store import Store


class RPC:
    def __init__(self,*args,**kwargs):
        self.calls=[];self.notifications=Queue();self.server_requests=Queue();self.turns=0;self.threads=0
    def request(self,method,params):
        self.calls.append((method,params))
        if method=='thread/start':self.threads+=1;return {'thread':{'id':'t'+str(self.threads)}}
        if method=='turn/start':self.turns+=1;return {'turn':{'id':'u'+str(self.turns)}}
        return {}
    def close(self):pass
    def interrupt(self,*args):pass


def until(condition):
    end=time.monotonic()+3
    while time.monotonic()<end:
        if condition():return
        time.sleep(.01)
    assert condition()


def attachments(store,monkeypatch):
    from briefloop import media
    image=store.add_source('chart.png','image needs visual reading')
    image_path=store.root/'sources'/(image['id']+'.png')
    image_path.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII='))
    pdf=store.add_source('report.pdf','PDF extracted text')
    pdf_path=store.root/'sources'/(pdf['id']+'.pdf');pdf_path.write_bytes(b'%PDF-synthetic-not-rendered')
    values={}
    for source,kind,original,pixels,pages in [(image,'image/png',image_path,image_path,None),(pdf,'application/pdf',pdf_path,None,8)]:
        values[source['id']]={'source_id':source['id'],'name':source['name'],'text_path':str(store.root/source['path']),
            'original_path':str(original),'media_type':kind,'image_path':str(pixels) if pixels else None,
            'width':1 if pixels else None,'height':1 if pixels else None,'pages':pages,'needs_visual':True,
            'status':'ready','error':None,'rendered_pages':[]}
    original_reader=media.source_attachment
    monkeypatch.setattr(media,'source_attachment',lambda current,sid:dict(values[sid]) if sid in values else original_reader(current,sid))
    return image,pdf,values


def test_start_and_steer_keep_native_image_and_source_anchor(tmp_path,monkeypatch):
    store=Store(tmp_path/'workspace');image,_,values=attachments(store,monkeypatch)
    manager=HarnessManager(store,RPC)
    config={'model':'vendor/unknown-vision-model','model_provider':'configured-responses','effort':None,'permission':'read-only'}
    sid=manager.create_session(runtime=config)['id']
    first=manager.send(sid,'',source_ids=[image['id']])
    until(lambda:manager.snapshot(sid)['session']['turn_id']=='u1')
    assert manager.snapshot(sid)['messages'][0]['text']=='请查看附件。'
    start=next(params for method,params in manager.client.calls if method=='turn/start')
    expected={'type':'localImage','path':values[image['id']]['image_path']}
    assert expected in start['input']
    assert any(block['type']=='text' and image['id'] in block['text'] for block in start['input'])
    assert start['sandboxPolicy']=={'type':'readOnly','networkAccess':False}
    assert start['model']==config['model'] and 'effort' not in start
    assert next(params for method,params in manager.client.calls if method=='thread/start')['modelProvider']==config['model_provider']
    steer=manager.send(sid,'再查看标注',mode='steer',source_ids=[image['id']])
    until(lambda:any(m['id']==steer['id'] and m['status']=='delivered' for m in manager.snapshot(sid)['messages']))
    actual=next(params for method,params in manager.client.calls if method=='turn/steer')
    assert expected in actual['input'] and actual['expectedTurnId']=='u1'
    assert any(block['type']=='text' and image['id'] in block['text'] for block in actual['input'])
    assert first['source_ids']==[image['id']]
    manager.close()


def test_bad_image_fails_before_queue_and_provider_rejection_does_not_drop_pixels(tmp_path,monkeypatch):
    store=Store(tmp_path/'workspace');image,_,values=attachments(store,monkeypatch)
    manager=HarnessManager(store,RPC);sid=manager.create_session()['id']
    values[image['id']].update(status='failed',error='图片已损坏',image_path=None)
    with pytest.raises(ValueError,match='图片已损坏'):manager.send(sid,'',source_ids=[image['id']])
    assert manager.snapshot(sid)['messages']==[] and manager.client is None
    manager.close()
    values[image['id']].update(status='ready',error=None,image_path=values[image['id']]['original_path'])
    class NoVision(RPC):
        def request(self,method,params):
            result=super().request(method,params)
            if method=='turn/start':raise RuntimeError('configured provider does not accept image inputs')
            return result
    manager=HarnessManager(store,NoVision);sid=manager.create_session(runtime={'model':'vendor/custom'})['id']
    manager.send(sid,'look',source_ids=[image['id']])
    until(lambda:manager.snapshot(sid)['session']['status']=='failed')
    starts=[params for method,params in manager.client.calls if method=='turn/start']
    assert len(starts)==1 and any(block['type']=='localImage' for block in starts[0]['input'])
    assert starts[0]['model']=='vendor/custom'
    assert any('does not accept image' in event['data'].get('message','') for event in manager.snapshot(sid)['events'])
    manager.close()


def test_internal_handoff_retains_visual_paths_without_loading_every_pdf_page(tmp_path,monkeypatch):
    store=Store(tmp_path/'workspace');image,pdf,values=attachments(store,monkeypatch)
    run=store.create_run({'title':'review','objective':'read source charts'},[image['id'],pdf['id']])
    job=store.enqueue('generate',{'run_id':run['id']});folder=store.root/'jobs'/job['id'];folder.mkdir()
    generation_prompt(store,run,folder);packet=json.loads((folder/'input.json').read_text())
    assert packet['sources'][0]['image_path']==values[image['id']]['image_path']
    assert packet['sources'][1]['original_path']==values[pdf['id']]['original_path']
    assert packet['sources'][1]['pages']==8
    runtime=InteractiveRuntime(store,object())
    assert runtime._input_source_ids(job,folder)==[]
    brief=store.publish(run['id'],{'title':'review','markdown':'chart','citations':[{'source_id':image['id'],'locator':'image'},{'source_id':pdf['id'],'locator':'PDF p.3'}]})
    evaluation=folder/'evaluation';evaluation.mkdir();assessment_prompt(store,brief,evaluation)
    stage=stage_job(store,job,'evaluator',mode='single')
    assert runtime._input_source_ids(stage,evaluation)==[image['id'],pdf['id']]
    manager=HarnessManager(store,RPC)
    task=manager.start_internal('evaluate the cited sources',source_ids=runtime._input_source_ids(stage,evaluation))
    until(lambda:manager.snapshot(task.session_id)['session']['turn_id'] is not None)
    blocks=next(params for method,params in manager.client.calls if method=='turn/start')['input']
    assert len([block for block in blocks if block['type']=='localImage'])==1
    assert any(block['type']=='text' and pdf['id'] in block['text'] and 'application/pdf' in block['text'] for block in blocks)
    assert manager.snapshot(task.session_id)['messages'][0]['source_ids']==[image['id'],pdf['id']]
    manager.close()
