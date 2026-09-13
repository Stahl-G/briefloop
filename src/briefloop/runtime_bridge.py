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
import time
from .rpc_writer import PipeWriter
from .platform_support import OwnedProcess, cli_command


class RuntimeBridge:
    def __init__(self, *, node_binary=None):
        self.node_binary=node_binary if node_binary is not None else os.environ.get('BRIEFLOOP_NODE')
        self._lock=threading.RLock()
        self._start_lock=threading.Lock();self._closed=False;self._broken=None;self._writer=None
        self._pending={}
        self._events={}
        self._process=None

    @property
    def process(self):return self._process

    def _start(self):
        with self._start_lock:
            with self._lock:
                if self._closed:raise RuntimeError('Runtime bridge 已关闭')
                old=self._process
                if old is not None and old is not self._broken and old.poll() is None:return old,self._writer
            if old is not None:old.close_tree(timeout=.2)
            from .host_bins import SEARCH_HINT, find as _find_host_bin
            node=_find_host_bin(self.node_binary or 'node')
            if not node:
                raise RuntimeError('未找到可执行的 Node.js：'+str(self.node_binary or 'node')+
                    '。Bridge 引擎需要 Node.js 20+；请安装后重启服务，或将 BRIEFLOOP_NODE 设置为 Node 可执行文件路径；'+SEARCH_HINT)
            env={**os.environ,'BRIEFLOOP_PYTHON':sys.executable,
                 'BRIEFLOOP_PROCESS_HELPER':str(files('briefloop').joinpath('process_host.py'))}
            # Electron's Node mode belongs only to this bridge child, never the service.
            env.pop('ELECTRON_RUN_AS_NODE',None)
            if env.get('BRIEFLOOP_NODE_IS_ELECTRON')=='1':env['ELECTRON_RUN_AS_NODE']='1'
            proc=OwnedProcess([node,str(files('briefloop').joinpath('static/runtime-bridge.mjs'))],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,
                env=env)
            writer=PipeWriter(proc.stdin,lambda error:self._abort(proc,error))
            with self._lock:
                closed=self._closed
                if not closed:self._process=proc;self._writer=writer;self._broken=None
            if closed:
                writer.abort();proc.close_tree(timeout=.2)
                raise RuntimeError('Runtime bridge 已关闭')
            threading.Thread(target=self._read,args=(proc,),daemon=True).start()
            return proc,writer

    def _abort(self,proc,error):
        with self._lock:
            if self._process is proc:
                self._broken=proc
                pending=list(self._pending.values());self._pending.clear()
                sinks=list(self._events.values())
            else:pending=[];sinks=[]
        for future in pending:
            if not future.done():future.set_exception(error)
        for sink in sinks:sink.put({'kind':'end','status':'failed','error':str(error)})
        proc.close_tree(timeout=.2)

    def _read(self,proc):
        try:
            for line in proc.stdout:
                try:message=json.loads(line)
                except ValueError:continue
                if message.get('method')=='event':
                    event=message.get('params',{})
                    with self._lock:sink=self._events.get(event.get('execution_id')) if self._process is proc else None
                    if sink is not None:sink.put(event)
                    continue
                with self._lock:future=self._pending.pop(message.get('id'),None) if self._process is proc else None
                if future is None:continue
                if 'error' in message:future.set_exception(RuntimeError(message['error'].get('message','Bridge error')))
                else:future.set_result(message.get('result'))
        finally:
            with self._lock:writer=self._writer if self._process is proc else None
            if writer is not None:writer.abort(RuntimeError('Runtime bridge 已退出，接收状态未确认；未自动重发'))

    def call(self,method,params=None,timeout=30):
        deadline=time.monotonic()+timeout
        rid=uuid.uuid4().hex;future=Future();proc,writer=self._start()
        with self._lock:
            if self._closed or proc is self._broken or proc is not self._process:raise RuntimeError('Bridge 连接已变化；未自动重发')
            self._pending[rid]=future
        try:
            remaining=max(0,deadline-time.monotonic())
            writer.send({'id':rid,'method':method,'params':params or {}},timeout=min(5,remaining))
            return future.result(timeout=max(0,deadline-time.monotonic()))
        except TimeoutError:
            writer.abort(RuntimeError('Bridge 请求超时，接收状态未确认；未自动重发'))
            raise
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
            item['integrated']=item['id'] in BACKENDS
            item['available']=bool(item['installed'] and item['integrated'])
            if item['id'] in ('codex','opencode'):
                item['capabilities']={'chat':True,'cancel':True,'images':'unknown','resume':'unknown','restricted_reviewer':'unknown',
                    'permission_modes':['workspace-write','read-only'],'steer':item['id']=='codex'}
            item['diagnostic']=('本机 CLI 已找到；执行协议尚未接入' if item['installed'] and not item['available'] else item.get('error'))
        return {'runtimes':result,**({'diagnostic':diagnostic} if diagnostic else {})}

    def close(self):
        with self._lock:
            self._closed=True;writer=self._writer;proc=self._process
        if writer is not None:writer.abort()
        elif proc is not None:proc.close_tree(timeout=.2)
