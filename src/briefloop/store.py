"""Workspace SQLite store. No model calls or long waits inside transactions."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import uuid
from .models import Requirements, Settings, BriefDraft, Assessment, ROLE_NAMES, normalize_role_models, runtime_fields


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def content_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def semantic_signature(markdown):
    from markdown_it import MarkdownIt
    parts=[]
    for token in MarkdownIt('commonmark').enable('table').parse(markdown):
        if token.type=='inline':
            text=[];links=[]
            for child in token.children or []:
                if child.type in ('text','code_inline','image'):text.append(child.content)
                if child.type in ('softbreak','hardbreak'):text.append(' ')
                if child.type=='link_open':links.append(child.attrGet('href') or '')
            parts.append(' '.join(''.join(text).split()))
            if links:parts.append(dump(links))
        elif token.type in ('fence','code_block'):parts.append(token.content)
    return '\n'.join(parts)


class Conflict(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
 url TEXT, status TEXT NOT NULL, error TEXT, hash TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, requirements TEXT NOT NULL,
 source_ids TEXT NOT NULL, skill_id TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_sources(run_id TEXT NOT NULL REFERENCES runs(id), source_id TEXT NOT NULL REFERENCES sources(id), PRIMARY KEY(run_id,source_id));
CREATE TABLE IF NOT EXISTS briefs(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 parent_id TEXT REFERENCES briefs(id), author TEXT NOT NULL, markdown TEXT NOT NULL,
 hash TEXT NOT NULL, detail TEXT NOT NULL, editor_document TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assessments(id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES briefs(id),
 data TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES briefs(id),
 kind TEXT NOT NULL, data TEXT NOT NULL, batch_id TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
 payload TEXT NOT NULL, result TEXT, error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT,
 kind TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, parent_id TEXT, content TEXT NOT NULL,
 targets TEXT NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL);
"""


class Store:
    def __init__(self, workspace):
        self.root = Path(workspace).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for d in ("sources", "jobs", "wiki", "exports"):
            (self.root/d).mkdir(exist_ok=True)
        self.db = self.root/"briefloop.db"
        with self.tx() as c:
            c.executescript(SCHEMA)
            if 'mode' not in {r['name'] for r in c.execute('PRAGMA table_info(runs)')}:
                c.execute("ALTER TABLE runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'normal'")
            c.execute("INSERT OR IGNORE INTO meta VALUES('settings', ?)", (dump(Settings().model_dump()),))
            c.execute("INSERT OR IGNORE INTO meta VALUES('schema', '1')")
            c.execute("INSERT OR IGNORE INTO meta VALUES('workspace_id', ?)",(dump(uid("workspace")),))

    @contextmanager
    def tx(self):
        c = sqlite3.connect(self.db, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def rows(self, query, args=()):
        with closing(sqlite3.connect(self.db, timeout=10)) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(query, args)]

    def one(self, table, id):
        if table not in {"sources", "runs", "briefs", "assessments", "jobs", "skills"}:
            raise ValueError("Unknown record type")
        result = self.rows(f"SELECT * FROM {table} WHERE id=?", (id,))
        if not result:
            raise ValueError("Record not found")
        return result[0]

    def meta(self, key, default=None):
        rows = self.rows("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_meta(self, key, value):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, dump(value)))

    def settings(self):
        result=Settings.model_validate(self.meta("settings")).model_dump()
        if result.get('model_provider') is None:
            result.pop('model_provider',None)
        result['role_models']={role:runtime_fields(config) for role,config in result['role_models'].items()}
        return result

    def add_source(self, name, text, *, url=None, error=None, source_id=None):
        sid = source_id or uid("src")
        path = self.root/"sources"/(sid+".txt")
        sha = content_hash(text)
        if path.exists() and path.read_text() != text:
            raise Conflict("Source snapshot cannot be overwritten")
        path.write_text(text)
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?)",
                      (sid, name, str(path.relative_to(self.root)), url, "failed" if error else "ready", error, sha, now()))
        return self.one("sources", sid)

    def source_text(self, sid):
        r = self.one("sources", sid)
        path = (self.root/r["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Invalid source path")
        text = path.read_text()
        if content_hash(text) != r["hash"]:
            raise Conflict("Source changed outside the application")
        return text

    def create_run(self, requirements, source_ids, **options):
        req = Requirements.model_validate(requirements)
        for sid in source_ids:
            self.one("sources", sid)
        if not source_ids and not req.allow_web:
            raise ValueError("请添加来源，或允许联网查找来源")
        rid = uid("run")
        with self.tx() as c:
            c.execute("INSERT INTO runs(id,requirements,source_ids,skill_id,created,mode) VALUES(?,?,?,?,?,?)", (rid, dump(req.model_dump()), dump(source_ids), options.get("skill_id",self.meta("active_skill")), now(), options.get("mode","normal")))
            if options.get("mode","normal")=="normal":
                c.execute("INSERT OR REPLACE INTO meta VALUES('requirements',?)", (dump(req.model_dump()),))
        return self.one("runs", rid)

    def attach_source(self, run_id, source_id):
        with self.tx() as c:
            c.execute('INSERT OR IGNORE INTO run_sources VALUES(?,?)',(run_id,source_id))

    def source_ids(self, run_id):
        run=self.one('runs',run_id)
        acquired=self.rows('SELECT source_id FROM run_sources WHERE run_id=? ORDER BY rowid',(run_id,))
        return list(dict.fromkeys(json.loads(run['source_ids'])+[r['source_id'] for r in acquired]))

    def publish(self, run_id, draft, *, version_id=None):
        draft = BriefDraft.model_validate(draft)
        self.one("runs", run_id)
        for ref in draft.citations:
            self.one("sources", ref.source_id)
        vid = version_id or uid("brief")
        sha = content_hash(draft.markdown)
        with self.tx() as c:
            existing = c.execute("SELECT hash FROM briefs WHERE id=?", (vid,)).fetchone()
            if existing:
                if existing["hash"] != sha:
                    raise Conflict("Completed draft differs")
            else:
                c.execute("INSERT INTO briefs VALUES(?,?,?,?,?,?,?,?,?)", (vid, run_id, None, "agent", draft.markdown, sha, dump(draft.model_dump(exclude={"markdown"})), None, now()))
            for ref in draft.citations:
                c.execute("INSERT OR IGNORE INTO run_sources VALUES(?,?)",(run_id,ref.source_id))
        return self.one("briefs", vid)

    def revise(self, base_version, markdown, editor_document=None):
        vid = uid("brief")
        with self.tx() as c:
            base = c.execute("SELECT * FROM briefs WHERE id=?", (base_version,)).fetchone()
            if not base:
                raise ValueError("Original brief missing")
            latest = c.execute("SELECT id FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (base["run_id"],)).fetchone()
            if latest["id"] != base_version:
                raise Conflict("稿件已有更新，请先保留本地编辑并重新加载最新版本")
            if markdown == base["markdown"]:
                return dict(base)
            c.execute("INSERT INTO briefs VALUES(?,?,?,?,?,?,?,?,?)", (vid, base["run_id"], base_version, "user", markdown, content_hash(markdown), base["detail"], dump(editor_document) if editor_document else None, now()))
            if semantic_signature(markdown)!=semantic_signature(base['markdown']):
                c.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?)", (uid("feedback"), vid, "revision", dump({"before": base_version, "after": vid}), None, now()))
        return self.one("briefs", vid)

    def assess(self, version_id, value):
        brief = self.one("briefs", version_id)
        assessment = Assessment.model_validate(value)
        if assessment.brief_hash != brief["hash"]:
            raise Conflict("评分对应的稿件内容与当前版本不一致")
        for f in assessment.findings:
            if f.source_id:
                self.one("sources", f.source_id)
        aid = uid("assessment")
        with self.tx() as c:
            c.execute("INSERT INTO assessments VALUES(?,?,?,?)", (aid, version_id, dump(assessment.model_dump()), now()))
        return self.one("assessments", aid)

    def comment(self, version_id, text):
        self.one("briefs", version_id)
        fid = uid("feedback")
        with self.tx() as c:
            c.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?)", (fid, version_id, "comment", dump({"text": text}), None, now()))
        return {"id": fid}

    def runtime_config(self):
        settings=self.settings()
        return runtime_fields(settings)

    def role_model_config(self, runtime=None):
        base=runtime or self.runtime_config()
        overrides=self.settings()['role_models']
        return {role:dict(overrides.get(role,base)) for role in ROLE_NAMES}

    def enqueue(self, kind, payload):
        runtime=runtime_fields(payload.get('runtime',self.runtime_config()))
        # Freeze inherited defaults too; later settings never mutate queued jobs.
        overrides=normalize_role_models(payload.get('role_models',self.role_model_config(runtime)))
        provider=payload.get('search_provider',self.settings()['search_provider'])
        if provider not in ('codex','tavily'):
            raise ValueError('无效搜索来源')
        payload={**payload,'runtime':runtime,'search_provider':provider,
                 'role_models':{role:runtime_fields(overrides.get(role,runtime)) for role in ROLE_NAMES}}
        jid = uid("job")
        with self.tx() as c:
            c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", (jid, kind, "queued", dump(payload), None, None, now(), now()))
        return self.one("jobs", jid)

    def search_provider_for_run(self, run_id):
        self.one('runs',run_id)
        for row in self.rows("SELECT payload FROM jobs WHERE kind='generate' ORDER BY rowid DESC"):
            payload=json.loads(row['payload'])
            if payload.get('run_id')==run_id:
                # Jobs predating provider selection used Codex, regardless of
                # the currently selected preference in this workspace.
                return payload.get('search_provider','codex')
        return self.settings()['search_provider']

    def update_job(self, jid, status, *, result=None, error=None):
        with self.tx() as c:
            c.execute("UPDATE jobs SET status=?,result=COALESCE(?,result),error=?,updated=? WHERE id=?", (status, dump(result) if result is not None else None, error, now(), jid))

    def event(self, job_id, kind, data):
        with self.tx() as c:
            c.execute("INSERT INTO events(job_id,kind,data,created) VALUES(?,?,?,?)", (job_id, kind, dump(data), now()))

    def bind_skill(self, skill_id):
        if skill_id:
            self.one("skills", skill_id)
        self.set_meta("active_skill", skill_id)
        self.event(None, "skill_binding", {"skill_id": skill_id})

    def snapshot(self):
        jobs=self.rows("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 30")
        for j in jobs:
            events=self.rows("SELECT data FROM events WHERE job_id=? AND kind='learning_progress' ORDER BY seq DESC LIMIT 1",(j['id'],))
            j['progress']=json.loads(events[0]['data']) if events else None
        from .length import length_stats
        runs=self.rows("SELECT * FROM runs ORDER BY created DESC")
        requirements={r['id']:json.loads(r['requirements']) for r in runs}
        briefs=self.rows("SELECT b.* FROM briefs b JOIN runs r ON r.id=b.run_id WHERE r.mode='normal' ORDER BY b.rowid DESC")
        for brief in briefs:
            req=requirements[brief['run_id']]
            # Historical requirements are not retroactively assigned a new budget.
            brief['length_stats']=length_stats(brief['markdown'],target_words=req.get('target_words'),max_words=req.get('max_words'))
        return {"workspace": self.root.name, "workspace_id":self.meta("workspace_id"), "requirements": self.meta("requirements"), "settings": self.settings(),
                "sources": self.rows("SELECT * FROM sources ORDER BY created"),
                "runs": runs,
                "briefs": briefs,
                "assessments": self.rows("SELECT * FROM assessments ORDER BY rowid DESC"),
                "feedback": self.rows("SELECT * FROM feedback ORDER BY rowid DESC LIMIT 100"),
                "jobs": jobs,
                "skills": self.rows("SELECT * FROM skills ORDER BY rowid DESC"),
                "active_skill": self.meta("active_skill"),
                "wiki": (self.root/"wiki/index.md").read_text() if (self.root/"wiki/index.md").exists() else ""}
