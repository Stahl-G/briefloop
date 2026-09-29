import json
from pathlib import Path
import pytest
from briefloop.store import Store, Conflict
from briefloop.runtime import Worker
from briefloop.interactive_runtime import InteractiveRuntime, _usable_output


class ArtifactHarness:
    def __init__(self, produce):self.starts=0;self.sessions={};self.produce=produce
    def create_session(self,title,runtime,cwd):
        sid='s'+str(len(self.sessions));self.sessions[sid]={'session':{'id':sid,'status':'idle','lifecycle':'active'},'messages':[],'events':[]};return self.sessions[sid]['session']
    def start_internal(self,text,**kw):
        self.starts+=1;self.produce(Path(kw['cwd']))
        self.sessions[kw['session_id']]['messages'].append({'id':kw['message_id'],'role':'user','text':kw['display_text'],'status':'completed','turn_id':'t'+str(self.starts)})
    def snapshot(self,sid,after=0):return self.sessions[sid]
    def cancel(self,sid):pass
