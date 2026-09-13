"""Bidirectional Codex transport for the interactive UI.

Connection initialization performs no turn/model request. Existing exec jobs are
not taken over. UI integration opts into this transport for new sessions.
"""
from concurrent.futures import Future
from . import __version__
import json
from pathlib import Path
from queue import Queue
import shutil
import subprocess
import threading
import time
from .rpc_writer import PipeWriter
from .platform_support import OwnedProcess


class AppServerClient:
    def __init__(self, log_directory):
        root=Path(log_directory);root.mkdir(parents=True,exist_ok=True)
        from .host_bins import SEARCH_HINT, find as _find_host_bin
        executable=_find_host_bin('codex')
        if not executable:raise RuntimeError('未找到 Codex CLI；'+SEARCH_HINT)
        self.notifications=Queue();self.server_requests=Queue()
        self._pending={};self._lock=threading.Lock();self._sequence=0;self._closed=False
        self._stderr=(root/'app-server.stderr.log').open('a')
        self.process=OwnedProcess([executable,'--enable','multi_agent','app-server','--listen','stdio://'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self._stderr,text=True,bufsize=1,parent_death=True)
        self._writer=PipeWriter(self.process.stdin,self._abort)
        self._reader=threading.Thread(target=self._read,daemon=True);self._reader.start()
        try:
            self.identity=self.request('initialize',{'clientInfo':{'name':'briefloop','version':__version__},'capabilities':{'experimentalApi':True}})
            self.notify('initialized',{})
        except BaseException:
            self.close();raise

    def _abort(self,error):
        with self._lock:
            self._closed=True
            pending=list(self._pending.values());self._pending.clear()
        for future in pending:
            if not future.done():future.set_exception(error)
        self.process.close_tree(timeout=.2)

    def _send(self,message,timeout=5):
        with self._lock:
            if self._closed or self.process.poll() is not None:raise RuntimeError('会话服务已退出')
        self._writer.send(message,timeout=timeout)

    def request(self,method,params,timeout=20):
        deadline=time.monotonic()+timeout
        with self._lock:
            self._sequence+=1;identifier=self._sequence;future=Future();self._pending[identifier]=future
        try:
            self._send({'id':identifier,'method':method,'params':params},timeout=min(5,max(0,deadline-time.monotonic())))
            return future.result(timeout=max(0,deadline-time.monotonic()))
        except TimeoutError:
            self._writer.abort(RuntimeError('会话请求超时，接收状态未确认；未自动重发'))
            raise
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
                with self._lock:future=self._pending.pop(message.get('id'),None)
                if future is not None and not future.done():
                    if 'error' in message:future.set_exception(RuntimeError(str(message['error'])))
                    else:future.set_result(message.get('result'))
        finally:
            self._writer.abort(RuntimeError('会话连接已断开；未自动重发消息'))

    def interrupt(self,thread_id,turn_id):
        return self.request('turn/interrupt',{'threadId':thread_id,'turnId':turn_id})

    def close(self):
        self._writer.abort()
        if threading.current_thread() is not self._reader:self._reader.join(timeout=1)
        self._stderr.close()
