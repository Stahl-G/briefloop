"""One local NDJSON bridge per service. Store remains the execution authority."""
from concurrent.futures import Future
from importlib.resources import files
import json
import shutil
import subprocess
import threading
import uuid
import queue


class RuntimeBridge:
    def __init__(self):
        self._lock=threading.RLock()
        self._pending={}
        self._events={}
        self._process=None

    @property
    def process(self):return self._process

    def _start(self):
        if self._process is not None and self._process.poll() is None:return
        node=shutil.which('node')
        if not node:raise RuntimeError('需要本机 Node.js 20+ 来运行多 Runtime bridge')
        self._process=subprocess.Popen([node,str(files('briefloop').joinpath('static/runtime-bridge.mjs'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
        threading.Thread(target=self._read,args=(self._process,),daemon=True).start()

    def _read(self,proc):
        try:
            for line in proc.stdout:
                try:message=json.loads(line)
                except ValueError:continue
                if message.get('method')=='event':
                    event=message.get('params',{})
                    with self._lock:sink=self._events.get(event.get('execution_id'))
                    if sink is not None:sink.put(event)
                    continue
                with self._lock:future=self._pending.pop(message.get('id'),None)
                if future is None:continue
                if 'error' in message:future.set_exception(RuntimeError(message['error'].get('message','Bridge error')))
                else:future.set_result(message.get('result'))
        finally:
            with self._lock:
                if self._process is proc:
                    for future in self._pending.values():future.set_exception(RuntimeError('Runtime bridge 已退出'))
                    self._pending.clear()
                    for sink in self._events.values():sink.put({'kind':'end','status':'failed','error':'Bridge 已退出，宿主接收与执行状态未确认；未自动重发'})

    def call(self,method,params=None,timeout=30):
        rid=uuid.uuid4().hex;future=Future()
        with self._lock:
            self._start();self._pending[rid]=future
            try:
                self._process.stdin.write(json.dumps({'id':rid,'method':method,'params':params or {}})+'\n')
                self._process.stdin.flush()
            except (BrokenPipeError,OSError):
                self._pending.pop(rid,None)
                raise RuntimeError('Bridge 未确认接收请求；不会自动重发') from None
        try:return future.result(timeout)
        finally:
            with self._lock:self._pending.pop(rid,None)

    def subscribe(self,execution_id):
        with self._lock:
            if execution_id in self._events:raise ValueError('Execution already observed')
            sink=queue.Queue();self._events[execution_id]=sink;return sink

    def unsubscribe(self,execution_id):
        with self._lock:self._events.pop(execution_id,None)

    def discover(self):
        result=self.call('discover',timeout=15)
        from .backends import BACKENDS
        for item in result:
            item['available']=bool(item['installed'] and item['id'] in BACKENDS)
            if item['id'] in ('codex','opencode'):
                item['capabilities']={'chat':True,'cancel':True,'images':'unknown','resume':'unknown','restricted_reviewer':'unknown'}
            item['diagnostic']=('本机 CLI 已找到；执行协议尚未接入' if item['installed'] and not item['available'] else item.get('error'))
        return {'runtimes':result}

    def close(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.stdin.close()
                try:self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:self._process.kill()
