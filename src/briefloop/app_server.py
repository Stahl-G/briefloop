"""Bidirectional Codex transport for the interactive UI.

Connection initialization performs no turn/model request. Existing exec jobs are
not taken over. UI integration opts into this transport for new sessions.
"""
from concurrent.futures import Future
import json
from pathlib import Path
from queue import Queue
import shutil
import subprocess
import threading


class AppServerClient:
    def __init__(self, log_directory, *, model='gpt-5.6-luna', effort='high'):
        root=Path(log_directory);root.mkdir(parents=True,exist_ok=True)
        executable=shutil.which('codex')
        if not executable:raise RuntimeError('Codex CLI 未安装')
        self.model=model;self.effort=effort;self.notifications=Queue();self.server_requests=Queue()
        self._pending={};self._lock=threading.Lock();self._sequence=0
        self._stderr=(root/'app-server.stderr.log').open('a')
        self.process=subprocess.Popen([executable,'--enable','multi_agent','-c','model='+json.dumps(model),'-c','model_reasoning_effort='+json.dumps(effort),'app-server','--listen','stdio://'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self._stderr,text=True,bufsize=1,start_new_session=True)
        self._reader=threading.Thread(target=self._read,daemon=True);self._reader.start()
        try:
            self.identity=self.request('initialize',{'clientInfo':{'name':'briefloop','version':'0.1.0'},'capabilities':{'experimentalApi':True}})
            self.notify('initialized',{})
        except BaseException:
            self.close();raise

    def _send(self,message):
        with self._lock:
            if self.process.poll() is not None:raise RuntimeError('会话服务已退出')
            self.process.stdin.write(json.dumps(message,ensure_ascii=False)+'\n');self.process.stdin.flush()

    def request(self,method,params,timeout=20):
        with self._lock:
            self._sequence+=1;identifier=self._sequence;future=Future();self._pending[identifier]=future
        try:
            self._send({'id':identifier,'method':method,'params':params})
            return future.result(timeout=timeout)
        finally:
            with self._lock:self._pending.pop(identifier,None)

    def notify(self,method,params):self._send({'method':method,'params':params})

    def answer(self,identifier,result):self._send({'id':identifier,'result':result})

    def reject(self,identifier,message):
        self._send({'id':identifier,'error':{'code':-32601,'message':message}})

    def _read(self):
        try:
            for line in self.process.stdout:
                try:message=json.loads(line)
                except ValueError:continue
                if 'method' in message:
                    (self.server_requests if 'id' in message else self.notifications).put(message)
                    continue
                with self._lock:future=self._pending.get(message.get('id'))
                if future is not None and not future.done():
                    if 'error' in message:future.set_exception(RuntimeError(str(message['error'])))
                    else:future.set_result(message.get('result'))
        finally:
            with self._lock:
                for future in self._pending.values():
                    if not future.done():future.set_exception(RuntimeError('会话连接已断开；未自动重发消息'))

    def start_thread(self,cwd):
        return self.request('thread/start',{'cwd':str(cwd),'model':self.model,'approvalPolicy':'never','sandbox':'workspace-write'})

    def start_turn(self,thread_id,text,message_id):
        return self.request('turn/start',{'threadId':thread_id,'model':self.model,'effort':self.effort,'clientUserMessageId':message_id,'input':[{'type':'text','text':text,'text_elements':[]}]})

    def steer(self,thread_id,turn_id,text,message_id):
        return self.request('turn/steer',{'threadId':thread_id,'expectedTurnId':turn_id,'clientUserMessageId':message_id,'input':[{'type':'text','text':text,'text_elements':[]}]})

    def interrupt(self,thread_id,turn_id):
        return self.request('turn/interrupt',{'threadId':thread_id,'turnId':turn_id})

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=5)
        self._reader.join(timeout=2);self._stderr.close()
