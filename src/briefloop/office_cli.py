"""Optional OfficeCLI enhancement: detection, quality checks and page renders.

OfficeCLI is an optional local binary (docx/xlsx/pptx validate, issue listing
and screenshots). Every entry point here is best effort: a missing binary, a
disabled workspace switch or any subprocess failure records "not run" without
touching the export job, the delivery gate or the audit fingerprints. Command
forms are fixed to the ones the shipped CLI actually speaks — `validate <file>
--json`, `view <file> issues --json` and `view <file> screenshot --page N -o
<out> --json`; a top-level `screenshot` subcommand does not exist.
"""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import threading
import time

from . import host_bins, platform_support
from .store import dump, now, uid

BINARY = 'officecli'
OFFICE_SUFFIXES = ('.docx', '.xlsx', '.pptx')
# Every officecli invocation otherwise runs its daily self-update check: it
# writes ~/.officecli/config.json, spawns a background process that contacts
# the vendor's release mirror (and replaces a non-package-managed binary), and
# refreshes skill files it installed into other AI tools. A background quality
# check must not do any of that on the user's behalf.
QUIET_ENV = {'OFFICECLI_SKIP_UPDATE': '1', 'OFFICECLI_NO_AUTO_INSTALL': '1'}


def _env():
    return {**os.environ, **QUIET_ENV}
# One HTTP request (office-check: validate+issues, office-preview: all pages)
# shares this budget, so service shutdown is never blocked by one caller.
REQUEST_BUDGET_SECONDS = 180
# Below this remaining budget no new subprocess starts; the step is recorded
# as an error instead of leaving the request hanging on a shrinking timeout.
MIN_STEP_SECONDS = 5
VALIDATE_TIMEOUT = 120
ISSUES_TIMEOUT = 120
SCREENSHOT_TIMEOUT = 180
# One workbook enhancement shares the per-request budget; standalone `batch` is
# a single open/apply/save cycle, and the trailing `close` only matters if a
# resident process was left behind.
ENHANCE_BATCH_TIMEOUT = 150
ENHANCE_CLOSE_TIMEOUT = 30
VERSION_TIMEOUT = 5
VERSION_TTL_SECONDS = 600
HINT_TTL_SECONDS = 30
MAX_ISSUE_ITEMS = 50
MAX_IMAGE_PIXELS = 40_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS office_checks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,file_sha256 TEXT NOT NULL,
 file_path TEXT NOT NULL,job_id TEXT,version_id TEXT,status TEXT NOT NULL,summary TEXT NOT NULL,
 data TEXT NOT NULL,tool_version TEXT,created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS office_checks_file ON office_checks(file_sha256,kind);
"""

_version_lock = threading.Lock()
_version_cache = {}
_hint_cache = {'path': None, 'expires': 0.0}


def find():
    """Absolute officecli path via PATH and the shared known install dirs."""
    return host_bins.find(BINARY)


def _clear_caches():
    """Test seam: forget memoized lookups after the host environment changes."""
    with _version_lock:
        _version_cache.clear()
    _hint_cache.update(path=None, expires=0.0)


def version(path):
    """`officecli --version` once per binary mtime; failures cache as None."""
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (str(path), stat.st_mtime)
    with _version_lock:
        cached = _version_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
    value = None
    try:
        probe = subprocess.run(platform_support.cli_command([str(path), '--version']),
                               stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               encoding='utf-8', timeout=VERSION_TIMEOUT, check=True, env=_env())
        value = probe.stdout.strip().split('\n')[0][:160] or None
    except (OSError, subprocess.SubprocessError):
        value = None
    with _version_lock:
        _version_cache[key] = (time.monotonic() + VERSION_TTL_SECONDS, value)
    return value


def _enabled(store):
    return store.settings().get('officecli_enabled') is True


def enhancement_ready(store):
    """Can an export be enhanced right now? Frozen into export fingerprints, so
    flipping the switch changes the identity instead of reusing a base file."""
    return _enabled(store) and find() is not None


def require_enabled(store):
    """Shared 400-message gate for the explicit check and preview entry points."""
    if not _enabled(store) or not find():
        raise ValueError('未检测到 OfficeCLI 或未开启：请在设置中开启后重试')


def capability(store):
    """Discovery record for /api/runtimes; detection is not a usability claim."""
    path = find()
    enabled = _enabled(store)
    installed = path is not None
    return {'id': BINARY, 'name': 'OfficeCLI', 'installed': installed, 'path': path,
            'version': version(path) if installed else None,
            'enabled': enabled, 'available': installed and enabled,
            'diagnostic': ('已检测到本机 OfficeCLI；开启后导出 Word 时运行质检与渲染。检测不验证质检可用性。' if installed
                           else '未检测到 OfficeCLI；' + host_bins.SEARCH_HINT)}


def snapshot_hint(store):
    """Constant-shape /api/state hint: filesystem lookup only, never a subprocess."""
    clock = time.monotonic()
    if _hint_cache['expires'] <= clock:
        _hint_cache.update(path=find(), expires=clock + HINT_TTL_SECONDS)
    return {'installed': _hint_cache['path'] is not None, 'enabled': _enabled(store)}


def run_json(args, *, timeout, deadline=None):
    """Run one officecli command and parse its {success,data,error} envelope.

    Exit codes 0 and 2 mean the command ran (2 carries warnings); anything
    else is a failure envelope. Structured failures are returned, never raised.
    """
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= MIN_STEP_SECONDS:
            return {'ok': False, 'data': None, 'reason': '请求时间预算用尽'}
        timeout = min(timeout, remaining)
    try:
        command = platform_support.cli_command([str(item) for item in args])
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                   text=True, encoding='utf-8', timeout=timeout, check=False,
                                   env=_env())
    except subprocess.TimeoutExpired:
        return {'ok': False, 'data': None, 'reason': f'officecli 执行超时（{int(timeout)} 秒）'}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {'ok': False, 'data': None, 'reason': '无法运行 officecli：' + type(exc).__name__}
    envelope = None
    try:
        parsed = json.loads((completed.stdout or '').strip())
        if isinstance(parsed, dict):
            envelope = parsed
    except ValueError:
        envelope = None
    error = _envelope_error(envelope)
    if completed.returncode not in (0, 2):
        return {'ok': False, 'data': None, 'reason': error or f'officecli 退出码 {completed.returncode}'}
    if envelope is None or not envelope.get('success'):
        return {'ok': False, 'data': None,
                'reason': error or ('officecli 输出不是有效的 JSON 信封' if envelope is None else 'officecli 未返回成功结果')}
    if envelope.get('data') is None:
        return {'ok': False, 'data': None, 'reason': 'officecli 未返回数据'}
    return {'ok': True, 'data': envelope['data'], 'reason': None}


def _envelope_error(envelope):
    if not isinstance(envelope, dict):
        return None
    error = envelope.get('error')
    if isinstance(error, dict):
        return str(error.get('error') or error.get('code') or error) or None
    return str(error) if error else None


def _file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _record_check(store, kind, target, digest, outcome, job_id, version_id, tool_version):
    """Persist the newest row per (file_sha256, kind); returns its response view."""
    created = now()
    if outcome['ok']:
        data = outcome['data']
        if kind == 'validate':
            status, summary, payload = 'ok', str(data), {'summary': str(data)}
        else:
            count = data.get('count') if isinstance(data, dict) else None
            items = data.get('issues') if isinstance(data, dict) else None
            count = count if type(count) is int and count >= 0 else 0
            items = [item for item in items if isinstance(item, dict)][:MAX_ISSUE_ITEMS] if isinstance(items, list) else []
            found = count or len(items)
            status = 'issues' if found else 'ok'
            summary = f'{found} 项质检发现' if found else '未发现质检问题'
            payload = {'count': count, 'issues': items}
    else:
        status, summary, payload = 'error', outcome['reason'], {'reason': outcome['reason']}
    with store.tx() as c:
        c.execute('DELETE FROM office_checks WHERE file_sha256=? AND kind=?', (digest, kind))
        c.execute('INSERT INTO office_checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (uid('office'), kind, digest, str(target), job_id, version_id, status,
                   summary, dump(payload), tool_version, created))
    view = {'status': status, 'created': created}
    if status == 'error':
        view['reason'] = summary
    elif kind == 'validate':
        view['summary'] = summary
    else:
        view.update(count=payload['count'], items=list(payload['issues']))
    return view


def _row_view(row):
    data = json.loads(row['data'])
    view = {'status': row['status'], 'created': row['created']}
    if row['status'] == 'error':
        view['reason'] = row['summary']
    elif row['kind'] == 'validate':
        view['summary'] = row['summary']
    else:
        view.update(count=int(data.get('count') or 0),
                    items=list(data.get('issues') or [])[:MAX_ISSUE_ITEMS])
    return view


def check_file(store, path, *, job_id=None, version_id=None, deadline=None):
    """Best-effort validate + issues for one exported file; never raises.

    Silent no-op unless the switch is on, the suffix is an Office format and
    the binary is found: no subprocess, no rows, no events.
    """
    try:
        target = Path(path)
        if not _enabled(store) or target.suffix.lower() not in OFFICE_SUFFIXES or not target.is_file():
            return None
        binary = find()
        if not binary:
            return None
        if deadline is None:
            deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
        digest = _file_sha256(target)
        tool_version = version(binary)
        steps = (('validate', [binary, 'validate', str(target), '--json'], VALIDATE_TIMEOUT),
                 ('issues', [binary, 'view', str(target), 'issues', '--json'], ISSUES_TIMEOUT))
        views = {kind: _record_check(store, kind, target, digest,
                                     run_json(args, timeout=limit, deadline=deadline),
                                     job_id, version_id, tool_version)
                 for kind, args, limit in steps}
        summary = {'tool': BINARY, 'tool_version': tool_version,
                   'validate': views['validate'], 'issues': views['issues']}
        if job_id:
            store.event(job_id, 'office_check', summary)
        return summary
    except Exception:
        return None


def enhance_workbook(store, path, plan, *, cancelled=None):
    """Apply one planned batch of workbook enhancements on a staging copy.

    The whole plan goes out as a single standalone `batch` command — that form
    is one open/save cycle, so the file is on disk the moment officecli exits
    (verified against 1.0.152: openpyxl reads formulas, cached values and
    number formats immediately). Splitting it into per-cell `set` calls would
    take the resident delayed-flush path instead and leave edits invisible.
    The defensive `close` afterwards is idempotent ("already saved to disk;
    nothing to close") and covers a resident that some other tool left behind.

    A failed batch step is reported through the summary; officecli writes data
    bars as an x14 extension, which openpyxl drops on re-save — callers verify
    read-only and never re-save an enhanced workbook. Like check_file this
    keeps the base artifact on any failure. Cancellation is cooperative at
    command boundaries (the current bounded subprocess finishes first).
    """
    try:
        if cancelled is not None and cancelled.is_set(): raise InterruptedError('Excel 制作已停止')
        commands = (plan or {}).get('commands') or []
        if not commands:
            return {'applied': False, 'reason': None}
        if not _enabled(store) or not find():
            return {'applied': False, 'reason': '未检测到 OfficeCLI 或未开启'}
        binary = find()
        deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
        outcome = run_json([binary, 'batch', str(path), '--commands', json.dumps(commands, ensure_ascii=False), '--json'],
                           timeout=ENHANCE_BATCH_TIMEOUT, deadline=deadline)
        if cancelled is not None and cancelled.is_set(): raise InterruptedError('Excel 制作已停止')
        if not outcome['ok']:
            return {'applied': False, 'reason': outcome['reason']}
        summary = (outcome['data'] or {}).get('summary') if isinstance(outcome['data'], dict) else None
        failed = summary.get('failed') if isinstance(summary, dict) else None
        if type(failed) is int and failed > 0:
            return {'applied': False, 'reason': f'officecli batch 有 {failed} 条命令未成功'}
        run_json([binary, 'close', str(path)], timeout=ENHANCE_CLOSE_TIMEOUT, deadline=deadline)
        if cancelled is not None and cancelled.is_set(): raise InterruptedError('Excel 制作已停止')
        return {'applied': True, 'reason': None}
    except InterruptedError:
        raise
    except Exception as exc:
        return {'applied': False, 'reason': '无法运行 officecli：' + type(exc).__name__}


def _view_for_digest(store, digest):
    rows = {row['kind']: row for row in
            store.rows('SELECT * FROM office_checks WHERE file_sha256=? ORDER BY rowid', (digest,))}
    if not rows:
        return None
    newest = max(rows.values(), key=lambda row: (row['created'], row['id']))
    job_kind = None
    if newest.get('job_id'):
        found = store.rows('SELECT kind FROM jobs WHERE id=?', (newest['job_id'],))
        job_kind = found[0]['kind'] if found else None
    view = {'file_sha256': digest, 'job_id': newest['job_id'], 'job_kind': job_kind,
            'created': newest['created'], 'tool': BINARY, 'tool_version': newest['tool_version']}
    for kind in ('validate', 'issues'):
        view[kind] = _row_view(rows[kind]) if kind in rows else {'status': 'error', 'reason': '未运行'}
    return view


def version_office_view(store, version_id):
    """Check view for the newest complete export/release artifact of a version.

    Deliberately still Word-only: this view feeds /api/version-checks and the
    review status, both of which describe the formal Word deliverable. An
    Excel export (a working artifact, checkable from its own task card and
    POST /api/office-check) must not take over those delivery-bound surfaces.
    """
    rows = store.rows("SELECT result FROM jobs WHERE kind IN ('export_docx','release') AND status='complete' "
                      "AND json_extract(payload,'$.version_id')=? ORDER BY rowid DESC", (version_id,))
    for row in rows:
        digest = (json.loads(row['result'] or '{}') or {}).get('sha256')
        if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest):
            view = _view_for_digest(store, digest)
            if view is not None:
                return view
    return None


def run_check_for_job(store, job_id):
    """Explicit re-run entry for POST /api/office-check."""
    from .release import safe_file
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError('缺少任务编号')
    job = store.one('jobs', job_id)
    if job['kind'] not in ('export_docx', 'export_xlsx', 'release'):
        raise ValueError('只能对 Word/Excel 导出或正式交付任务运行 OfficeCLI 质检')
    if job['status'] != 'complete':
        raise ValueError('任务尚未完成，无法质检')
    result = json.loads(job['result'] or '{}')
    if not result.get('path') or not result.get('sha256'):
        raise ValueError('任务没有可质检的工件')
    path = safe_file(store.root, result['path'])
    if _file_sha256(path) != result['sha256']:
        raise ValueError('工件已变化，与任务记录不一致，请重新生成')
    require_enabled(store)
    if path.suffix.lower() not in OFFICE_SUFFIXES:
        raise ValueError('该文件类型暂不支持 OfficeCLI 质检')
    check_file(store, path, job_id=job['id'],
               version_id=(json.loads(job['payload']) or {}).get('version_id'))
    view = _view_for_digest(store, result['sha256'])
    if view is None:
        raise ValueError('OfficeCLI 质检未产生记录')
    return view


def _png_size(payload):
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != 'PNG':
                raise ValueError('not png')
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError('too large')
            image.verify()
            return width, height
    except (UnidentifiedImageError, OSError, SyntaxError, EOFError) as exc:
        raise ValueError('officecli 渲染输出不是有效 PNG') from exc


def _render_directory(store, digest):
    directory = store.root / 'office' / 'renders' / digest
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.resolve().is_relative_to((store.root / 'office' / 'renders').resolve()):
        raise ValueError('渲染缓存路径无效')
    return directory


def _atomic_bytes(path, data):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _cached_render(directory, digest, page):
    path = directory / f'page-{page:04d}.png'
    record = path.with_suffix('.json')
    if not path.is_file() or not record.is_file():
        return None
    try:
        metadata = json.loads(record.read_text(encoding='utf-8'))
        if not isinstance(metadata, dict) or metadata.get('source_sha256') != digest or metadata.get('page') != page:
            return None
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != metadata.get('image_sha256'):
            return None
        width, height = _png_size(payload)
    except (OSError, ValueError):
        return None
    return {'page': page, 'path': str(path), 'width': width, 'height': height,
            'image_sha256': metadata['image_sha256'], 'digest': digest,
            'tool_version': metadata.get('tool_version'), 'cached': True}


def render_page(store, path, page, *, deadline=None, timeout=SCREENSHOT_TIMEOUT):
    """Render one page via `view <file> screenshot --page N -o <out> --json`.

    Output lands in the content-addressed cache office/renders/<sha>/ with a
    hash-bound sidecar, mirroring the PDF page render cache.
    """
    binary = find()
    target = Path(path)
    if not binary:
        raise ValueError('未检测到 OfficeCLI 或未开启')
    if type(page) is not int or page < 1:
        raise ValueError('页码必须为从 1 开始的整数')
    digest = _file_sha256(target)
    directory = _render_directory(store, digest)
    cached = _cached_render(directory, digest, page)
    if cached:
        return cached
    staging = directory / f'.render-{page}.tmp.png'
    try:
        outcome = run_json([binary, 'view', str(target), 'screenshot', '--page', str(page),
                            '-o', str(staging), '--json'], timeout=timeout, deadline=deadline)
        if not outcome['ok']:
            raise ValueError('预览失败，不影响文件本身：' + str(outcome['reason']))
        payload = staging.read_bytes()
        width, height = _png_size(payload)
        destination = directory / f'page-{page:04d}.png'
        os.replace(staging, destination)
    finally:
        if staging.exists():
            staging.unlink()
    metadata = {'source_sha256': digest, 'page': page,
                'image_sha256': hashlib.sha256(payload).hexdigest(),
                'tool': BINARY, 'tool_version': version(binary), 'width': width, 'height': height}
    _atomic_bytes(destination.with_suffix('.json'), json.dumps(metadata, sort_keys=True).encode())
    return {'page': page, 'path': str(destination), 'width': width, 'height': height,
            'image_sha256': metadata['image_sha256'], 'digest': digest,
            'tool_version': metadata['tool_version'], 'cached': False}


def _resolve_target(store, kind, identity):
    from .release import get_release, safe_file
    identity = str(identity)
    if kind == 'source_id':
        from .media import source_files
        source, _provenance, original = source_files(store, identity)
        if original is None:
            raise ValueError('该来源未保留可渲染的 Office 原件')
        return {'kind': 'source', 'id': source['id'], 'name': source['name'], 'path': original}
    if kind == 'job_id':
        job = store.one('jobs', identity)
        if job['kind'] not in ('export_docx', 'export_xlsx', 'release'):
            raise ValueError('不是 Word/Excel 导出或正式交付任务')
        if job['status'] != 'complete':
            raise ValueError('任务尚未完成，无法预览')
        result = json.loads(job['result'] or '{}')
        if not result.get('path'):
            raise ValueError('任务没有可预览的工件')
        return {'kind': 'export', 'id': job['id'], 'name': Path(result['path']).name,
                'path': safe_file(store.root, result['path'])}
    release = get_release(store, identity)
    if not release.get('result'):
        raise ValueError('正式件尚未完成，无法预览')
    return {'kind': 'release', 'id': release['id'], 'name': 'report.docx',
            'path': safe_file(store.root, release['result']['path'])}


def render_preview(store, body):
    """POST /api/office-preview: render 1–4 pages of a source, export or release."""
    from .media import MAX_RENDER_PAGES
    require_enabled(store)
    binary = find()
    targets = [key for key in ('source_id', 'job_id', 'release_id') if body.get(key)]
    if len(targets) != 1:
        raise ValueError('请指定来源、导出任务或正式件三者之一')
    pages = body.get('pages', [1])
    if (not isinstance(pages, (list, tuple)) or not 1 <= len(pages) <= MAX_RENDER_PAGES
            or any(type(page) is not int or page < 1 for page in pages)):
        raise ValueError(f'请指定 1 至 {MAX_RENDER_PAGES} 个从 1 开始的页码')
    pages = list(dict.fromkeys(pages))
    target = _resolve_target(store, targets[0], body[targets[0]])
    if target['path'].suffix.lower() not in OFFICE_SUFFIXES:
        raise ValueError('本地渲染预览仅支持 docx/xlsx/pptx 文件')
    deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
    rendered, cached_all, incomplete, reason = [], True, False, None
    for page in pages:
        try:
            found = render_page(store, target['path'], page, deadline=deadline)
        except ValueError as exc:
            if deadline - time.monotonic() <= MIN_STEP_SECONDS:
                incomplete, reason = True, '请求时间预算用尽，仅返回已完成页面'
                break
            raise
        cached_all = cached_all and found['cached']
        rendered.append({'page': page,
                         'url': f"/api/office-image?digest={found['digest']}&page={page}",
                         'width': found['width'], 'height': found['height']})
    return {'target': {'kind': target['kind'], 'id': target['id'], 'name': target['name']},
            'tool': BINARY, 'tool_version': version(binary),
            'cached': bool(rendered) and cached_all, 'pages': rendered,
            'incomplete': incomplete, 'reason': reason}


def office_image(store, digest, page):
    """Cached render bytes for GET /api/office-image, three-way hash bound."""
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('无效的渲染缓存标识')
    try:
        number = int(str(page))
    except (TypeError, ValueError):
        number = -1
    if number < 1:
        raise ValueError('页码必须为从 1 开始的整数')
    found = _cached_render(_render_directory(store, digest), digest, number)
    if not found:
        raise ValueError('渲染缓存不存在或绑定不一致')
    return Path(found['path']).read_bytes()
