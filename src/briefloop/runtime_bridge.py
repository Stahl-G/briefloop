"""One local NDJSON bridge per service. Store remains the execution authority."""
from concurrent.futures import Future
from importlib.resources import files
import json
import os
import subprocess
import threading
import uuid
import queue
import sys
from .platform_support import OwnedProcess, cli_command


class RuntimeBridge:
    def __init__(self, *, node_binary=None):
        self.node_binary=node_binary if node_binary is not None else os.environ.get('BRIEFLOOP_NODE')
        self._lock=threading.RLock()
        self._pending={}
        self._events={}
        self._process=None

    @property
    def process(self):return self._process

    def _start(self):
        if self._process is not None and self._process.poll() is None:return
        if self._process is not None:self._process.close_tree()
        from .host_bins import SEARCH_HINT, find as _find_host_bin
        node=_find_host_bin(self.node_binary or 'node')
        if not node:
            raise RuntimeError('未找到可执行的 Node.js：'+str(self.node_binary or 'node')+
                '。Bridge 引擎需要 Node.js 20+；请安装后重启服务，或将 BRIEFLOOP_NODE 设置为 Node 可执行文件路径；'+SEARCH_HINT)
        self._process=OwnedProcess([node,str(files('briefloop').joinpath('static/runtime-bridge.mjs'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,
            env={**os.environ,'BRIEFLOOP_PYTHON':sys.executable,
                 'BRIEFLOOP_PROCESS_HELPER':str(files('briefloop').joinpath('process_host.py'))})
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
        try:result=self.call('discover',timeout=15)
        except (RuntimeError,OSError,TimeoutError) as exc:
            # Codex and Opencode have independent Python transports. A missing
            # or failed bridge must not hide either native installation.
            from .host_bins import find as find_host_bin
            from .backends import BACKEND_LABELS
            result=[]
            for name in ('codex','opencode'):
                path=find_host_bin(name);version=None;error=None
                if path:
                    try:
                        probe=subprocess.run(cli_command([path,'--version']),capture_output=True,text=True,encoding='utf-8',timeout=5,check=True)
                        version=probe.stdout.strip().split('\n')[0][:160]
                    except (OSError,subprocess.SubprocessError):error='Version probe failed'
                result.append({'id':name,'name':BACKEND_LABELS[name],'path':path,'installed':bool(path),
                    'version':version,'status':'detected' if path else 'not_installed',
                    'protocol':'native-manager','error':error})
            diagnostic=str(exc)
        else:diagnostic=None
        from .backends import BACKENDS
        for item in result:
            item['available']=bool(item['installed'] and item['id'] in BACKENDS)
            if item['id'] in ('codex','opencode'):
                item['capabilities']={'chat':True,'cancel':True,'images':'unknown','resume':'unknown','restricted_reviewer':'unknown',
                    'permission_modes':['workspace-write','read-only'],'steer':item['id']=='codex'}
            item['diagnostic']=('本机 CLI 已找到；执行协议尚未接入' if item['installed'] and not item['available'] else item.get('error'))
        return {'runtimes':result,**({'diagnostic':diagnostic} if diagnostic else {})}

    def close(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.stdin.close()
                try:self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:pass
            if self._process is not None:self._process.close_tree()
