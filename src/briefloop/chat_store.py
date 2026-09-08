"""Small durable chat journal sharing the workspace's SQLite database."""
import json
from .store import now, uid, dump

SCHEMA = '''
CREATE TABLE IF NOT EXISTS chat_sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL,thread_id TEXT,
 turn_id TEXT,status TEXT NOT NULL,runtime TEXT NOT NULL,cwd TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_messages(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES chat_sessions(id),
 role TEXT NOT NULL,text TEXT NOT NULL,status TEXT NOT NULL,mode TEXT NOT NULL,source_ids TEXT NOT NULL,
 turn_id TEXT,item_id TEXT,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,rpc_id TEXT NOT NULL,data TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,
 kind TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL);
'''

class ChatStore:
    def __init__(self, store):
        self.store=store
        with store.tx() as c:
            c.executescript(SCHEMA)
            c.execute("UPDATE chat_requests SET status='expired' WHERE status='pending'")
            columns={r['name'] for r in c.execute('PRAGMA table_info(chat_messages)')}
            for name,definition in (('runtime',"TEXT NOT NULL DEFAULT '{}'"),('prompt',"TEXT"),('allow_web',"INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:c.execute('ALTER TABLE chat_messages ADD COLUMN '+name+' '+definition)
            c.execute("UPDATE chat_sessions SET status='interrupted',turn_id=NULL WHERE status IN ('running','starting')")
            c.execute("UPDATE chat_messages SET status='interrupted' WHERE status IN ('sending','streaming','delivered')")

    @staticmethod
    def decode(row):
        if row is None: raise KeyError('会话或消息不存在')
        out=dict(row)
        for key in ('runtime','source_ids','data'):
            if key in out:out[key]=json.loads(out[key])
        return out

    def create(self,title,runtime,cwd):
        sid=uid('chat');date=now()
        with self.store.tx() as c:
            c.execute('INSERT INTO chat_sessions VALUES(?,?,NULL,NULL,?,?,?,?,?)',(sid,title,'idle',dump(runtime),str(cwd),date,date))
        return self.session(sid)

    def session(self,sid):
        with self.store.tx() as c:return self.decode(c.execute('SELECT * FROM chat_sessions WHERE id=?',(sid,)).fetchone())

    def sessions(self):
        with self.store.tx() as c:return [self.decode(r) for r in c.execute('SELECT * FROM chat_sessions ORDER BY updated DESC')]

    def update(self,sid,**values):
        allowed={'title','thread_id','turn_id','status','runtime','cwd'}
        if not set(values)<=allowed:raise ValueError('Invalid session update')
        if 'runtime' in values:values['runtime']=dump(values['runtime'])
        values['updated']=now()
        with self.store.tx() as c:c.execute('UPDATE chat_sessions SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),sid))

    def message(self,sid,text,role='user',status='queued',mode='queue',source_ids=None,mid=None,item_id=None,turn_id=None,runtime=None,prompt=None,allow_web=False):
        mid=mid or uid('msg');date=now()
        with self.store.tx() as c:
            c.execute('INSERT OR IGNORE INTO chat_messages(id,session_id,role,text,status,mode,source_ids,turn_id,item_id,created,updated,runtime,prompt,allow_web) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(mid,sid,role,text,status,mode,dump(source_ids or []),turn_id,item_id,date,date,dump(runtime or {}),prompt,int(allow_web)))
            result=self.decode(c.execute('SELECT * FROM chat_messages WHERE id=?',(mid,)).fetchone())
        if result['session_id']!=sid:raise ValueError('Message id belongs to another session')
        return result

    def patch_message(self,mid,**values):
        if not set(values)<={'text','status','turn_id'}:raise ValueError('Invalid message update')
        values['updated']=now()
        with self.store.tx() as c:c.execute('UPDATE chat_messages SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),mid))

    def event(self,sid,kind,data):
        with self.store.tx() as c:c.execute('INSERT INTO chat_events(session_id,kind,data,created) VALUES(?,?,?,?)',(sid,kind,dump(data),now()))

    def snapshot(self,sid,after=0,private=False):
        session=self.session(sid)
        with self.store.tx() as c:
            messages=[self.decode(r) for r in c.execute('SELECT * FROM chat_messages WHERE session_id=? ORDER BY created,rowid',(sid,))]
            usage_row=c.execute("SELECT data FROM chat_events WHERE session_id=? AND kind='thread/tokenUsage/updated' ORDER BY seq DESC LIMIT 1",(sid,)).fetchone()
            token_usage=json.loads(usage_row['data']).get('tokenUsage') if usage_row else None
            requests=[self.decode(r) for r in c.execute('SELECT id,session_id,data,status,created FROM chat_requests WHERE session_id=? ORDER BY created',(sid,))]
            events=[self.decode(r) for r in c.execute('SELECT * FROM chat_events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 1000',(sid,after))]
        if not private:
            for message in messages:message.pop('prompt',None)
        return {'session':session,'messages':messages,'events':events,'requests':requests,'token_usage':token_usage}

    def add_request(self,sid,rpc_id,data):
        rid=uid('question')
        with self.store.tx() as c:c.execute('INSERT INTO chat_requests VALUES(?,?,?,?,?,?)',(rid,sid,dump(rpc_id),dump(data),'pending',now()))
        return rid

    def request(self,rid):
        with self.store.tx() as c:
            row=c.execute('SELECT * FROM chat_requests WHERE id=?',(rid,)).fetchone()
            result=self.decode(row);result['rpc_id']=json.loads(result['rpc_id']);return result

    def request_status(self,rid,status):
        with self.store.tx() as c:c.execute('UPDATE chat_requests SET status=? WHERE id=?',(status,rid))
