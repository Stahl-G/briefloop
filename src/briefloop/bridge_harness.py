"""Native CLI transports projected into the existing ChatStore, not another agent loop."""
import base64
import hashlib
import json
import queue
import threading
import time
from pathlib import Path
from .opencode_harness import OpencodeHarness
from .harness import InternalRun
from .store import uid


class BridgeHarness(OpencodeHarness):
    def __init__(self,store,bridge,backend):
        super().__init__(store)
        self.bridge=bridge;self.client=bridge;self.backend=backend
        self._active_executions={}

    def _config(self,runtime):
        value={'permission':'runtime-native','model':'default','backend':self.backend,**(runtime or {})}
        if value['backend']!=self.backend:raise ValueError('请新建会话切换执行引擎')
        if value.get('review_root') or value['permission']!='runtime-native':
            raise ValueError('此 CLI 尚未验证受限文件或联网隔离；请选择支持该权限的执行引擎')
        if not isinstance(value['model'],str) or not value['model'].strip():raise ValueError('请输入模型 ID')
        return value

    def list_models(self,refresh=False):
        return self.bridge.call('list_models',{'runtime_id':self.backend,'cwd':str(self.store.root)},timeout=30)

    def _host_instructions(self,sid,session,config,allow_web):
        """BriefLoop's own workspace contract, framed ahead of the user's message.

        Bridge hosts keep their own default persona (Claude Code and friends) unless
        we send this; a true system channel is not available over the bridge, so the
        block rides in the same payload, as the upstream Open Design bridge does.
        It is sent when the native session is created and only re-sent when it
        changed, so a resumed session is not charged for it every turn.
        """
        from .chat_tools import chat_instructions
        internal=bool(self.store.rows("SELECT seq FROM chat_events WHERE session_id=? AND kind='session/internal' LIMIT 1",(sid,)))
        text=chat_instructions(self.store,config,internal=internal,allow_web=allow_web,backend=self.backend)
        digest=hashlib.sha256(text.encode()).hexdigest()
        if session.get('thread_id'):
            rows=self.store.rows("SELECT data FROM chat_events WHERE session_id=? AND kind='thread/instructions' ORDER BY seq DESC LIMIT 1",(sid,))
            if rows and json.loads(rows[0]['data']).get('hash')==digest:return ''
        self.chat.event(sid,'thread/instructions',{'hash':digest,'backend':self.backend,'model':config.get('model')})
        return text

    def send(self,session_id,text,mode='queue',source_ids=None,runtime=None,message_id=None,display_text=None,allow_web=False):
        if mode not in ('queue','steer'):raise ValueError('无效发送方式')
        if mode=='steer':raise ValueError('此 CLI 尚未接入运行中追加；请排队发送')
        if not isinstance(text,str) or not text.strip():raise ValueError('请输入消息')
        session=self.chat.session(session_id)
        if session['runtime'].get('backend','codex')!=self.backend:raise ValueError('切换执行引擎请新建会话')
        config=self._config({**session['runtime'],**(runtime or {})})
        for source in source_ids or []:self.store.one('sources',source)
        mid=message_id or uid('msg')
        with self._lock:
            if session['lifecycle']!='active':raise ValueError('请先恢复该会话')
            prior=next((m for m in self.snapshot(session_id)['messages'] if m['id']==mid),None)
            if prior:return prior
            message=self.chat.message(session_id,display_text or text,source_ids=source_ids,mid=mid,runtime=config,
                                      prompt=text if display_text is not None else None,allow_web=allow_web)
            self._cancel_requested.discard(session_id)
            self.chat.event(session_id,'message/queued',{'messageId':mid,'mode':'queue'})
            self._schedule(session_id)
        message.pop('prompt',None);return message

    def start_internal(self,text,*,session_id=None,runtime=None,cwd=None,job_id=None,display_text=None,
                       allow_web=False,message_id=None,search_provider=None,source_ids=None):
        runtime={'permission':'runtime-native',**(runtime or {}),'backend':self.backend}
        if search_provider is not None:runtime['search_provider']=search_provider
        if session_id is None:session_id=self.create_session('简报任务',runtime,cwd)['id']
        self.chat.event(session_id,'session/internal',{})
        if job_id:self.chat.event(session_id,'job/attached',{'jobId':job_id})
        message=self.send(session_id,text,runtime=runtime,display_text=display_text,allow_web=allow_web,message_id=message_id,source_ids=source_ids)
        return InternalRun(session_id,message['id'])

    def _dispatch(self,sid,epoch):
        mid=None;execution=None;status='failed'
        try:
            session=self.chat.session(sid)
            message=next((m for m in self.chat.snapshot(sid,private=True)['messages'] if m['status']=='queued'),None)
            if message is None:return
            mid=message['id'];execution=mid
            config=self._config(message['runtime']);text,files=self._input(message,session['cwd'])
            if not message['allow_web']:text+='\n本轮不主动检索网络来源，仅使用已提供材料。'
            images=[]
            if files:
                folder=self.store.root/'runtime-inputs'/mid;folder.mkdir(parents=True,exist_ok=True)
                for i,file in enumerate(files):
                    if not file['url'].startswith('data:image/'):raise ValueError('Bridge 只接收已归一化图片附件')
                    file_path=folder/(str(i)+'.png');file_path.write_bytes(base64.b64decode(file['url'].split(',',1)[1]));images.append(str(file_path))
            sink=self.bridge.subscribe(execution)
            with self._lock:
                self._active_executions[sid]=execution
                self.chat.patch_message(mid,status='sending',turn_id=mid)
                self.chat.update(sid,turn_id=mid,status='starting',runtime=config)
                self.chat.event(sid,'runtime/admission',{'execution_id':execution,'status':'pending','backend':self.backend})
            if sid in self._cancel_requested:status='cancelled';return
            instructions=self._host_instructions(sid,session,config,bool(message['allow_web']))
            if instructions:instructions+='\n\n（以上工作区约定是执行环境说明，不要原文复述给用户。）\n\n---\n\n'
            params={'execution_id':execution,'runtime_id':self.backend,'cwd':session['cwd'],'prompt':text,
                    'model':config['model'],'permission':'runtime-native','allow_web':None,
                    'images':images,
                    # The host owns its search tools; grant them only when this turn asked for web access.
                    'web_tools':bool(message['allow_web'])}
            if instructions:params['prompt']=instructions+text
            if session.get('thread_id'):params['session_id']=session['thread_id']
            try:self.bridge.call('start',params,timeout=15)
            except TimeoutError:
                self.chat.event(sid,'runtime/admission',{'execution_id':execution,'status':'unknown'})
                raise RuntimeError('宿主是否接收请求尚未确认；已保留 execution_id，未自动重发') from None
            self.chat.event(sid,'runtime/admission',{'execution_id':execution,'status':'accepted'})
            self.chat.patch_message(mid,status='delivered');self.chat.update(sid,status='running')
            assistant=self.chat.message(sid,'',role='assistant',status='streaming',turn_id=mid,runtime=config)
            output='';reasoning='';tools={};started=time.monotonic()
            while True:
                if sid in self._cancel_requested:self.bridge.call('cancel',{'execution_id':execution})
                minutes=self.store.settings()['timeout_minutes']
                if minutes>0 and time.monotonic()-started>minutes*60:
                    self.bridge.call('cancel',{'execution_id':execution});raise TimeoutError('运行超过本轮时间上限')
                try:event=sink.get(timeout=.5)
                except queue.Empty:continue
                kind=event['kind']
                if kind=='text':
                    output+=event.get('text','');self.chat.patch_message(assistant['id'],text=output)
                elif kind=='reasoning':
                    reasoning+=event.get('text','');self.chat.patch_message(assistant['id'],reasoning=reasoning)
                elif kind=='session':
                    self.chat.update(sid,thread_id=event['session_id'])
                    self.chat.event(sid,'runtime/session',{'execution_id':execution,**event})
                elif kind=='tool':
                    key=event.get('id') or uid('tool');tools[key]={**tools.get(key,{}),**event}
                    tool=tools[key];complete=tool.get('status') in ('completed','failed','error')
                    item={'id':key,'type':'runtime_tool','tool':tool.get('name','工具'),'status':tool.get('status','running')}
                    self.chat.event(sid,'item/completed' if complete else 'item/started',{'item':item,'turnId':mid})
                    if complete:
                        from .execution_records import journal_tool
                        journal_tool(self.chat,sid,mid,key,tool.get('name','tool'),tool.get('input',{}),tool.get('output',''),status=tool['status'],native_session=self.chat.session(sid).get('thread_id'))
                elif kind=='question':
                    options=event.get('options',[])
                    rid=self.chat.add_request(sid,{'execution_id':execution,'request_id':event['request_id']},
                        {'questions':[{'id':'permission','question':event.get('title','CLI 请求权限'),
                          'options':[{'label':o.get('name') or o.get('optionId'),'description':o.get('kind','')} for o in options]}],
                         'native_options':options})
                    self.chat.event(sid,'runtime/question',{'requestId':rid})
                elif kind=='usage':
                    usage=event.get('usage') or {}
                    self.chat.event(sid,'thread/tokenUsage/updated',{'tokenUsage':{'last':{'inputTokens':usage.get('input_tokens',usage.get('input')),'outputTokens':usage.get('output_tokens',usage.get('output'))},'raw':usage,'backend':self.backend}})
                elif kind=='error':self.chat.event(sid,'error',{'message':event.get('message','CLI 执行失败')})
                elif kind=='end':
                    status=event.get('status','failed')
                    if event.get('error'):self.chat.event(sid,'error',{'message':event['error']})
                    break
        except Exception as exc:
            if mid:self.chat.event(sid,'error',{'message':str(exc)})
        finally:
            with self._lock:
                self._active_executions.pop(sid,None);self._busy.discard(sid)
                if execution:self.bridge.unsubscribe(execution)
                if mid:
                    for message in self.snapshot(sid)['messages']:
                        if message.get('turn_id')==mid and message['status'] in ('sending','delivered','streaming'):
                            self.chat.patch_message(message['id'],status=status)
                    for request in self.snapshot(sid)['requests']:
                        if request['status']=='pending':self.chat.request_status(request['id'],'expired')
                    self.chat.update(sid,turn_id=None,status='idle' if status=='completed' else status)
                    self.chat.event(sid,'turn/'+status,{'turnId':mid,'status':status})
                    if status=='completed':self._schedule(sid)

    def answer(self,session_id,request_id,answers):
        request=self.chat.request(request_id)
        if request['session_id']!=session_id or request['status']!='pending':raise ValueError('问题已结束或不属于当前会话')
        values=(answers.get('permission') or {}).get('answers',[])
        option=next((o for o in request['data']['native_options'] if (o.get('name') or o.get('optionId')) in values),None)
        if option is None:raise ValueError('请选择宿主提供的权限选项')
        result=self.bridge.call('answer',{**request['rpc_id'],'option_id':option['optionId']})
        self.chat.request_status(request_id,'answered');return result

    def cancel(self,session_id):
        with self._lock:
            self._cancel_requested.add(session_id)
            for message in self.snapshot(session_id)['messages']:
                if message['status']=='queued':self.chat.patch_message(message['id'],status='cancelled')
            execution=self._active_executions.get(session_id)
        if execution:return self.bridge.call('cancel',{'execution_id':execution})
        return {'cancelled':False}

    def close(self):
        for sid in list(self._active_executions):self.cancel(sid)
