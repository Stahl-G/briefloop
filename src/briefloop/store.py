"""Workspace SQLite store. No model calls or long waits inside transactions."""
from contextlib import contextmanager, closing, nullcontext
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
                if child.type=='image':
                    from urllib.parse import urlsplit,parse_qs
                    src=child.attrGet('src') or ''
                    if src.startswith('briefloop-figure:'):
                        identity=src.split(':',1)[1]
                    else:
                        url=urlsplit(src)
                        # Only local API expressions are aliases, not arbitrary
                        # external URLs with the same query string.
                        identity=(parse_qs(url.query).get('id',[''])[0]
                                  if not url.scheme and not url.netloc and url.path=='/api/figure' else '')
                    links.append('image:'+('briefloop-figure:'+identity if identity else src))
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
CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,revision INTEGER NOT NULL,
 parent_id TEXT,source_hash TEXT NOT NULL,status TEXT NOT NULL,spec TEXT NOT NULL,created TEXT NOT NULL,error TEXT,
 origin TEXT NOT NULL DEFAULT 'upload');
CREATE TABLE IF NOT EXISTS company_facts(id TEXT PRIMARY KEY,fact_key TEXT NOT NULL,value TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id),locator TEXT NOT NULL,effective_date TEXT NOT NULL,
 origin TEXT NOT NULL,status TEXT NOT NULL,previous_id TEXT,created TEXT NOT NULL,resolved_at TEXT);
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
            from .evidence import SCHEMA as EVIDENCE_SCHEMA
            c.executescript(EVIDENCE_SCHEMA)
            from .review import SCHEMA as REVIEW_SCHEMA
            c.executescript(REVIEW_SCHEMA)
            from .conflicts import SCHEMA as CONFLICT_SCHEMA
            c.executescript(CONFLICT_SCHEMA)
            from .release import SCHEMA as RELEASE_SCHEMA
            from .source_updates import SCHEMA as SOURCE_UPDATE_SCHEMA
            c.executescript(RELEASE_SCHEMA)
            c.executescript(SOURCE_UPDATE_SCHEMA)
            if 'mode' not in {r['name'] for r in c.execute('PRAGMA table_info(runs)')}:
                c.execute("ALTER TABLE runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'normal'")
            if 'origin' not in {r['name'] for r in c.execute('PRAGMA table_info(templates)')}:
                c.execute("ALTER TABLE templates ADD COLUMN origin TEXT NOT NULL DEFAULT 'upload'")
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
        backend=result.get('agent_backend','codex')
        shaped={}
        for role,config in result['role_models'].items():
            try:
                shaped[role]=runtime_fields(config,backend)
            except ValueError:
                # A backend switch can strand old model ids; keep them visible
                # so the UI can show them, and fail loudly only when enqueued.
                shaped[role]={key:config[key] for key in ('model','model_provider','reasoning_effort','model_variant') if key in config}
        result['role_models']=shaped
        return result

    def add_source(self, name, text, *, url=None, error=None, source_id=None, connection=None):
        """Persist a source; an optional transaction remains owned by its caller.

        Text files precede DB admission. On rollback a composing caller may remove
        only new, unreferenced files that it owns; existing snapshots stay intact.
        """
        sid = source_id or uid("src")
        path = self.root/"sources"/(sid+".txt")
        sha = content_hash(text)
        if path.exists() and path.read_bytes().decode("utf-8") != text:
            raise Conflict("Source snapshot cannot be overwritten")
        path.write_bytes(text.encode("utf-8"))
        with (self.tx() if connection is None else nullcontext(connection)) as c:
            c.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?)",
                      (sid, name, str(path.relative_to(self.root)), url, "failed" if error else "ready", error, sha, now()))
            source = dict(c.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone())
        return source

    def source_text(self, sid):
        r = self.one("sources", sid)
        path = (self.root/r["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Invalid source path")
        text = path.read_bytes().decode("utf-8")
        if content_hash(text) != r["hash"]:
            raise Conflict("Source changed outside the application")
        return text

    def create_run(self, requirements, source_ids, **options):
        req = Requirements.model_validate(requirements)
        selected = None
        if req.writing_mode=='internal_report' and self.settings().get('company_context_enabled') is None:
            raise ValueError('请先选择是否维护企业背景知识库；可选择不维护并继续报告')
        req.company_context_required=req.writing_mode=='internal_report' and self.settings().get('company_context_enabled') is True
        if self.settings().get('company_context_enabled') and not req.company_context_revision:
            from .company_context import snapshot
            req.company_context_revision=snapshot(self)['revision']
        if req.template_id:
            from .templates import template
            selected=template(self,req.template_id)
            if selected['status']!='ready':raise ValueError('所选模板尚未准备完成')
            if not req.sections:
                from .models import ReportSection
                req.sections=[ReportSection.model_validate(s) for s in selected['spec']['sections']]
        from .document_workflows import resolve_workflow, freeze_workflow, template_workflow_hint
        selection = resolve_workflow(req.model_dump(), template_workflow_hint(selected))
        req.workflow_id, req.workflow_variant = selection['id'], selection['variant']
        req.workflow_snapshot = freeze_workflow(selection)
        for sid in req.reference_source_ids:
            self.one("sources", sid)
        if set(source_ids) & set(req.reference_source_ids):
            raise ValueError("同一材料不能同时作为本期证据和风格参考，请选择用途")
        for sid in source_ids:
            self.one("sources", sid)
        if not source_ids and not req.allow_web:
            raise ValueError("请添加来源，或允许联网查找来源")
        rid = uid("run")
        with self.tx() as c:
            c.execute("INSERT INTO runs(id,requirements,source_ids,skill_id,created,mode) VALUES(?,?,?,?,?,?)", (rid, dump(req.model_dump()), dump(source_ids), options.get("skill_id",self.meta("active_skill")), now(), options.get("mode","normal")))
            if options.get("mode","normal")=="normal":
                c.execute("INSERT OR REPLACE INTO meta VALUES('requirements',?)", (dump(req.model_dump()),))
            if options.get("research_protocol"):
                c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ('research_protocol:'+rid, dump(options['research_protocol'])))
        return self.one("runs", rid)

    def attach_source(self, run_id, source_id):
        run=self.one('runs',run_id)
        if source_id in json.loads(run['requirements']).get('reference_source_ids',[]):
            raise ValueError('风格参考不能登记为本期证据')
        self.one('sources',source_id)
        with self.tx() as c:
            c.execute('INSERT OR IGNORE INTO run_sources VALUES(?,?)',(run_id,source_id))

    def source_ids(self, run_id):
        run=self.one('runs',run_id)
        acquired=self.rows('SELECT source_id FROM run_sources WHERE run_id=? ORDER BY rowid',(run_id,))
        return list(dict.fromkeys(json.loads(run['source_ids'])+[r['source_id'] for r in acquired]))

    def publish(self, run_id, draft, *, version_id=None, parent_id=None, author='agent'):
        if author not in ('agent', 'example'):raise ValueError('无效稿件作者')
        draft = BriefDraft.model_validate(draft)
        from .document_model import document_hash, source_ids
        if draft.editor_document is not None:
            from .models import Citation
            present={ref.source_id for ref in draft.citations}
            draft.citations.extend(Citation(source_id=sid) for sid in source_ids(draft.editor_document) if sid not in present)
        run=self.one("runs", run_id)
        from .company_context import require_review
        company_review=require_review(self,run)
        if draft.reader_contract is not None:
            from .deliverable_spec import resolve,validate_reader_contract
            draft.reader_contract=validate_reader_contract(resolve(json.loads(run['requirements'])),draft.reader_contract)
        references=set(json.loads(run['requirements']).get('reference_source_ids',[]))
        if parent_id and not draft.reconciliation_id:
            parent=self.one('briefs',parent_id)
            if parent['run_id']!=run_id:raise Conflict('修订基础版本不属于本报告')
            # The comparison describes the source snapshot, not author approval.
            # Preserve it across rewrites; read() still reports stale inputs.
            draft.reconciliation_id=json.loads(parent['detail']).get('reconciliation_id')
        if draft.reconciliation_id:
            from .reconciliation import exists
            if not exists(self,run_id,draft.reconciliation_id):
                raise ValueError('稿件引用的对照记录不存在或不属于本报告：'+draft.reconciliation_id)
        for ref in draft.citations:
            try:self.one("sources", ref.source_id)
            except ValueError:
                # A made-up or mistyped id must say which one, not "Record not found".
                raise ValueError('引用的来源 '+ref.source_id+' 不在本工作区；请使用登记工具返回的真实 source_id') from None
            if ref.source_id in references:
                raise ValueError('风格参考不能作为报告事实引用')
        if draft.report_data is not None:
            from .report_tools import prepare_for_run
            prepared=prepare_for_run(self,run_id,draft.report_data.model_dump(mode='json'))
            draft.gaps=list(dict.fromkeys(draft.gaps+prepared['gaps']))
            from .models import Citation
            cited={(ref.source_id,ref.locator) for ref in draft.citations}
            for row in draft.report_data.records:
                for sid,locator in [(row.source_id,row.locator),(row.previous_source_id,row.previous_locator)]:
                    if sid and (sid,locator) not in cited:
                        draft.citations.append(Citation(source_id=sid,locator=locator));cited.add((sid,locator))
        from .figure_support import validate_figures
        assets=validate_figures(self,run_id,draft.markdown)
        draft.figures=[f['figure_id'] for f in assets]
        from .models import Citation
        cited={ref.source_id for ref in draft.citations}
        for figure in assets:
            for source_id in figure['source_ids']:
                if source_id not in cited:
                    draft.citations.append(Citation(source_id=source_id,locator=figure['caption']));cited.add(source_id)
        vid = version_id or uid("brief")
        sha = document_hash(draft.editor_document) if draft.editor_document is not None else content_hash(draft.markdown)
        detail=draft.model_dump(mode='json',exclude={'markdown','editor_document'})
        if draft.editor_document is not None:detail['document_schema']=1
        if company_review:detail['company_context']={'revision':company_review['revision'],'review':company_review}
        with self.tx() as c:
            if parent_id:
                parent=c.execute('SELECT run_id FROM briefs WHERE id=?',(parent_id,)).fetchone()
                if not parent or parent['run_id']!=run_id:raise Conflict('修订基础版本不属于本报告')
                latest=c.execute('SELECT id FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run_id,)).fetchone()
                if latest['id'] not in (parent_id,vid):raise Conflict('用户已修改报告，自动修订仅保留为建议')
            existing = c.execute("SELECT hash,run_id,detail FROM briefs WHERE id=?", (vid,)).fetchone()
            if existing:
                if existing["hash"] != sha or existing['run_id']!=run_id:
                    raise Conflict("Completed draft differs")
                old_detail=json.loads(existing['detail'])
                old_detail.setdefault('research_notes',[])
                old_detail.setdefault('reader_contract',None)
                old_detail.setdefault('reconciliation_id',None)
                if old_detail!=detail:raise Conflict('Completed draft metadata differs; save a new version')
            else:
                c.execute("INSERT INTO briefs VALUES(?,?,?,?,?,?,?,?,?)", (vid, run_id, parent_id, author, draft.markdown, sha, dump(detail), dump(draft.editor_document) if draft.editor_document is not None else None, now()))
            for ref in draft.citations:
                c.execute("INSERT OR IGNORE INTO run_sources VALUES(?,?)",(run_id,ref.source_id))
        return self.one("briefs", vid)

    def revise(self, base_version, markdown='', editor_document=None, *, author='user'):
        if author not in ('user','agent'):raise ValueError('无效修订作者')
        from .document_model import normalize_document, document_markdown, document_hash, source_ids
        if editor_document is not None:
            editor_document=normalize_document(editor_document)
            markdown=document_markdown(editor_document)
        if not markdown.strip():raise ValueError('报告正文不能为空')
        vid = uid("brief")
        with self.tx() as c:
            base = c.execute("SELECT * FROM briefs WHERE id=?", (base_version,)).fetchone()
            if not base:
                raise ValueError("Original brief missing")
            latest = c.execute("SELECT id FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (base["run_id"],)).fetchone()
            if latest["id"] != base_version:
                raise Conflict("稿件已有更新，请先保留本地编辑并重新加载最新版本")
            same_document=(editor_document is None and base['editor_document'] is None or
                           editor_document is not None and base['editor_document'] is not None and
                           normalize_document(json.loads(base['editor_document']))==editor_document)
            if markdown == base["markdown"] and same_document:
                return dict(base)
            detail=json.loads(base['detail'])
            if editor_document is not None:
                detail['document_schema']=1
                references=set(json.loads(self.one('runs',base['run_id'])['requirements']).get('reference_source_ids',[]))
                for sid in source_ids(editor_document):
                    self.one('sources',sid)
                    if sid in references:raise ValueError('风格参考不能作为报告事实引用')
                    if sid not in [x['source_id'] for x in detail.get('citations',[])]:
                        detail.setdefault('citations',[]).append({'source_id':sid,'locator':'','excerpt':''})
                    c.execute('INSERT OR IGNORE INTO run_sources VALUES(?,?)',(base['run_id'],sid))
            else:detail.pop('document_schema',None)
            from .figure_support import validate_figures
            detail['figures']=[f['figure_id'] for f in validate_figures(self,base['run_id'],markdown)]
            if detail.get('report_data'):
                import re
                # Saved input numbers do not change when a user edits the prose/table.
                if re.findall(r'[-+]?\d+(?:[.,]\d+)*',base['markdown'])!=re.findall(r'[-+]?\d+(?:[.,]\d+)*',markdown):
                    detail['report_data_needs_review']=True
            sha=document_hash(editor_document) if editor_document is not None else content_hash(markdown)
            c.execute("INSERT INTO briefs VALUES(?,?,?,?,?,?,?,?,?)", (vid, base["run_id"], base_version, author, markdown, sha, dump(detail), dump(editor_document) if editor_document is not None else None, now()))
            if author=='user' and semantic_signature(markdown)!=semantic_signature(base['markdown']):
                c.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?)", (uid("feedback"), vid, "revision", dump({"before": base_version, "after": vid}), None, now()))
        return self.one("briefs", vid)

    def attach_figures(self,base_version,markdown):
        base=self.one('briefs',base_version)
        from .figure_support import validate_figures
        from .figures import figure_ids
        import re
        strip=lambda text:re.sub(r'!\[(?:\\.|[^\]\\])*\]\(briefloop-figure:[^)]+\)','',text).strip()
        if strip(markdown)!=strip(base['markdown']):
            # Whitespace around inserted images may differ, but prose must remain.
            if re.sub(r'\s+',' ',strip(markdown))!=re.sub(r'\s+',' ',strip(base['markdown'])):
                raise ValueError('补图接口只允许插入图表，不改写已有正文')
        figures=validate_figures(self,base['run_id'],markdown)
        detail=json.loads(base['detail']);detail['figures']=[f['figure_id'] for f in figures]
        document=None
        if base.get('editor_document'):
            from .document_model import markdown_document,document_markdown,document_hash
            from collections import defaultdict,deque
            existing=defaultdict(deque)
            for block in json.loads(base['editor_document']).get('content',[]):
                existing[document_markdown({'type':'doc','content':[block]})].append(block)
            document=markdown_document(markdown)
            for index,block in enumerate(document.get('content',[])):
                key=document_markdown({'type':'doc','content':[block]})
                if existing[key]:document['content'][index]=existing[key].popleft()
            markdown=document_markdown(document)
        vid=uid('brief')
        with self.tx() as c:
            latest=c.execute('SELECT id FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(base['run_id'],)).fetchone()
            if latest['id']!=base_version:raise Conflict('稿件已更新，请对照最新版本补图')
            sha=document_hash(document) if document is not None else content_hash(markdown)
            c.execute('INSERT INTO briefs VALUES(?,?,?,?,?,?,?,?,?)',(vid,base['run_id'],base_version,'agent',markdown,sha,dump(detail),dump(document) if document is not None else None,now()))
        return self.one('briefs',vid)

    def validate_assessment(self, version_id, value):
        """Read-only admission checks shared by persistence and retry caching."""
        brief = self.one("briefs", version_id)
        assessment = Assessment.model_validate(value)
        if assessment.brief_hash != brief["hash"]:
            raise Conflict("评分对应的稿件内容与当前版本不一致")
        for f in assessment.findings:
            if f.source_id:
                self.one("sources", f.source_id)
        return assessment

    def assess(self, version_id, value):
        assessment = self.validate_assessment(version_id, value)
        aid = uid("assessment")
        with self.tx() as c:
            c.execute("INSERT INTO assessments VALUES(?,?,?,?)", (aid, version_id, dump(assessment.model_dump()), now()))
        return self.one("assessments", aid)

    def generated_by(self,version_id,job_id):
        root='brief_'+job_id[4:];seen=set()
        while version_id and version_id not in seen:
            seen.add(version_id)
            try:brief=self.one('briefs',version_id)
            except ValueError:return False
            if brief['author']!='agent':return False
            if version_id==root:return True
            version_id=brief['parent_id']
        return False

    def comment(self, version_id, text):
        self.one("briefs", version_id)
        fid = uid("feedback")
        with self.tx() as c:
            c.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?)", (fid, version_id, "comment", dump({"text": text}), None, now()))
        return {"id": fid}

    def runtime_config(self):
        settings=self.settings()
        # A fresh workspace ships the factory model with the gate still set; the
        # flag alone must not block programmatic runs that already have a model.
        if settings.get('model_selection_required') and not str(settings.get('model') or '').strip():raise ValueError('请先在设置中选择用于报告和学习的模型')
        return runtime_fields(settings,settings.get('agent_backend','codex'))

    def confirm_runtime_choice(self,backend,runtime):
        """A runtime the user actually ran counts as a chosen model.

        Chat sessions carry their own runtime, so without this the workspace could
        show a selected model while still refusing to start a report because the
        pending-selection flag was never cleared.
        """
        settings=self.settings()
        if not settings.get('model_selection_required'):return settings
        backend=backend or settings.get('agent_backend','codex')
        fields=runtime_fields(runtime or {},backend)
        if not str(fields.get('model') or '').strip():return settings
        if backend not in ('codex','opencode'):fields.update(model_provider=None,model_variant=None)
        updated=Settings.model_validate({**settings,**fields,'agent_backend':backend,'model_selection_required':False})
        self.set_meta('settings',updated.model_dump())
        return updated.model_dump()

    def role_model_config(self, runtime=None, backend=None):
        base=runtime or self.runtime_config()
        backend=backend or self.settings().get('agent_backend','codex')
        overrides=self.settings()['role_models']
        roles={}
        for role in ROLE_NAMES:
            candidate=overrides.get(role)
            if not candidate:
                roles[role]=dict(base)
                continue
            try:
                roles[role]=runtime_fields(candidate,backend)
            except ValueError:
                # A role model saved for another runtime cannot run here. Inherit the
                # main chain instead of failing the whole job; settings() still shows
                # the stranded value so it can be cleared or re-picked.
                roles[role]=dict(base)
        return roles

    def enqueue(self, kind, payload):
        if kind not in ('export_docx','release','audit_bundle','source_refresh'):
            from .backends import validate_backend
            from .models import normalize_search_provider
            backend=validate_backend(payload.get('agent_backend',self.settings().get('agent_backend','codex')))
            runtime=runtime_fields(payload['runtime'] if 'runtime' in payload else self.runtime_config(),backend)
            # Freeze inherited defaults too; later settings never mutate queued jobs.
            overrides=normalize_role_models(payload.get('role_models',self.role_model_config(runtime,backend)))
            provider=normalize_search_provider(payload.get('search_provider',self.settings()['search_provider']))
            payload={**payload,'agent_backend':backend,'runtime':runtime,'search_provider':provider,
                     'role_models':{role:runtime_fields(overrides.get(role,runtime),backend) for role in ROLE_NAMES}}
            if kind=='generate':
                payload.setdefault('auto_revision',self.settings()['auto_revision'])
                payload.setdefault('max_parallel',self.settings()['max_parallel'])
            if kind=='generate' and payload.get('run_id'):
                runs=self.rows('SELECT requirements FROM runs WHERE id=?',(payload['run_id'],))
                if runs and json.loads(runs[0]['requirements']).get('writing_mode')=='internal_report':payload.setdefault('reader_contract_required',True)
        jid = uid("job")
        with self.tx() as c:
            c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", (jid, kind, "queued", dump(payload), None, None, now(), now()))
        job = self.one("jobs", jid)
        from .task_notify import notify as _notify_task
        _notify_task(self, job, 'queued')
        return job

    def search_provider_for_run(self, run_id):
        from .models import normalize_search_provider
        self.one('runs',run_id)
        for row in self.rows("SELECT payload FROM jobs WHERE kind='generate' ORDER BY rowid DESC"):
            payload=json.loads(row['payload'])
            if payload.get('run_id')==run_id:
                # Jobs predating provider selection used native search, regardless of
                # the currently selected preference in this workspace.
                return normalize_search_provider(payload.get('search_provider'))
        return self.settings()['search_provider']

    def update_job(self, jid, status, *, result=None, error=None):
        # Terminal task notifications fire from the Worker's own settle/stop boundary
        # (_settle_job / stop_job), which is where production jobs actually finish.
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
        from .document_workflows import list_workflows, template_workflow_hint
        jobs=self.rows("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 30")
        for j in jobs:
            events=self.rows("SELECT data FROM events WHERE job_id=? AND kind='learning_progress' ORDER BY seq DESC LIMIT 1",(j['id'],))
            j['progress']=json.loads(events[0]['data']) if events else None
        from .length import length_stats
        runs=self.rows("SELECT * FROM runs ORDER BY created DESC")
        acquired={}
        for row in self.rows("SELECT run_id,source_id FROM run_sources ORDER BY rowid"):
            acquired.setdefault(row['run_id'],[]).append(row['source_id'])
        for run in runs:
            ids=list(dict.fromkeys(json.loads(run['source_ids'])+acquired.get(run['id'],[])))
            run['all_source_ids']=ids
            run['source_count']=len(ids)
        requirements={r['id']:json.loads(r['requirements']) for r in runs}
        briefs=self.rows("SELECT b.* FROM briefs b JOIN runs r ON r.id=b.run_id WHERE r.mode='normal' ORDER BY b.rowid DESC")
        for brief in briefs:
            req=requirements[brief['run_id']]
            # Historical requirements are not retroactively assigned a new budget.
            brief['length_stats']=length_stats(brief['markdown'],target_words=req.get('target_words'),max_words=req.get('max_words'))
        return {"workspace": self.root.name, "workspace_id":self.meta("workspace_id"), "requirements": self.meta("requirements"), "settings": self.settings(),
                "profile": self.meta("workspace_profile") or {},
                "workflows":list_workflows(),
                "templates":[{**row, 'workflow_hint':template_workflow_hint(row)} for row in self.rows('SELECT * FROM templates ORDER BY created DESC')],
                "conflicts":self.rows("SELECT id,status,data,run_id FROM conflicts WHERE status!='resolved' ORDER BY rowid DESC LIMIT 100"),
                "company_context_pending":self.rows("SELECT * FROM company_facts WHERE status='pending' ORDER BY rowid DESC"),
                "sources": self.rows("SELECT * FROM sources ORDER BY created"),
                "runs": runs,
                "briefs": briefs,
                "assessments": self.rows("SELECT * FROM assessments ORDER BY rowid DESC"),
                "feedback": self.rows("SELECT * FROM feedback ORDER BY rowid DESC LIMIT 100"),
                "jobs": jobs,
                "skills": self.rows("SELECT * FROM skills ORDER BY rowid DESC"),
                "active_skill": self.meta("active_skill"),
                "wiki": (self.root/"wiki/index.md").read_text() if (self.root/"wiki/index.md").exists() else ""}
