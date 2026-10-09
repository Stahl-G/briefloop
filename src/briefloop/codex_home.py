"""Durable Codex history owned by BriefLoop, outside writable report workspaces.

This separates history, not OS permissions. Shared configuration and file auth
are linked, never copied into reports or interpreted as model input.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib
from uuid import UUID


def _link(source, target):
    if not source.exists():
        return
    if target.is_symlink() and target.resolve() == source.resolve():
        return
    if target.exists() or target.is_symlink():
        raise RuntimeError(f'Codex 隔离配置存在独立文件，未覆盖：{target}')
    try:
        target.symlink_to(source, target_is_directory=source.is_dir())
    except FileExistsError:
        if not target.is_symlink() or target.resolve() != source.resolve():
            raise
    except OSError as exc:
        raise RuntimeError('无法建立 Codex 登录/配置链接；未回退到个人会话目录。'
                           '可用 BRIEFLOOP_CODEX_HOME 指定已配置并登录的独立 Codex 目录。') from exc


class CodexHome:
    def __init__(self, *, environ=None, state_root=None):
        env = dict(os.environ if environ is None else environ)
        self.source = Path(env.get('CODEX_HOME') or Path.home()/'.codex').expanduser().resolve()
        explicit = env.get('BRIEFLOOP_CODEX_HOME')
        base = Path(state_root or Path.home()/'.config'/'briefloop'/'codex-homes')
        self.root = (Path(explicit).expanduser() if explicit else
                     base/hashlib.sha256(str(self.source).encode()).hexdigest()[:20]).resolve()
        if self.root == self.source or self.root.is_relative_to(self.source):
            raise RuntimeError('BriefLoop 的 Codex 独立目录不能位于个人 Codex 目录内。')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        state_paths = [self.root/name for name in ('sessions', 'archived_sessions',
                       'session_index.jsonl', 'history.jsonl', 'log')]
        state_paths.extend(self.root.glob('*.sqlite*'))
        if any(not path.resolve().is_relative_to(self.root) for path in state_paths):
            raise RuntimeError('Codex 历史或数据库链接指向独立目录之外，未启动共享历史。')
        if not explicit:
            config = self.source/'config.toml'
            settings = tomllib.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
            if settings.get('cli_auth_credentials_store', 'file') != 'file':
                raise RuntimeError('当前 Codex 使用密钥链或非文件登录，不能自动复用到独立目录。'
                                   '请用 BRIEFLOOP_CODEX_HOME 指定已单独登录的 Codex 目录；个人会话未被修改。')
            for name in ('config.toml', 'auth.json', 'AGENTS.md', 'AGENTS.override.md',
                         'rules', 'skills', 'agents', 'plugins', 'hooks.json'):
                _link(self.source/name, self.root/name)
            for profile in self.source.glob('*.config.toml'):
                _link(profile, self.root/profile.name)
        # An inherited sqlite override would defeat history separation even with
        # a different CODEX_HOME. CLI overrides also beat a shared config value.
        self.env = {**env, 'CODEX_HOME': str(self.root), 'CODEX_SQLITE_HOME': str(self.root)}
        self.overrides = ['-c', 'sqlite_home='+json.dumps(str(self.root)),
                          '-c', 'log_dir='+json.dumps(str(self.root/'log'))]

    def import_bound_thread(self, thread_id):
        """Copy only an explicitly bound old thread; keep the old original intact.

        No SQLite internals, bulk history copying, or title-based ownership guess.
        Rollouts are copied as opaque bytes; their contents are never logged.
        """
        try:
            identity = str(UUID(thread_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError('Codex 会话 ID 无效，未读取历史目录') from exc
        if identity != thread_id:
            raise ValueError('Codex 会话 ID 无效，未读取历史目录')
        for folder in ('sessions', 'archived_sessions'):
            if any((self.root/folder).rglob('*'+identity+'.jsonl')):
                return
        candidates = [p for folder in ('sessions', 'archived_sessions')
                      for p in (self.source/folder).rglob('*'+identity+'.jsonl')
                      if not p.is_symlink() and p.resolve().is_relative_to(self.source/folder)]
        if not candidates:
            return  # Let Codex report its authoritative missing-thread error.
        if len(candidates) != 1:
            raise RuntimeError('旧 Codex 会话有多个历史文件，未自动选择或覆盖。')
        source = candidates[0]
        # Active rollout paths keep their date layout. Archived ones can sit at
        # the root; retain that relative layout and let Codex index the copy.
        source_base = next(self.source/name for name in ('sessions', 'archived_sessions')
                           if source.is_relative_to(self.source/name))
        target = self.root/'sessions'/source.relative_to(source_base)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        before = source.stat()
        fd, temporary = tempfile.mkstemp(prefix='.import-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as output, source.open('rb') as input_file:
                shutil.copyfileobj(input_file, output)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError('旧 Codex 会话仍在写入，请等待该任务结束后再继续；原记录未修改。')
            # Exclusive publication: a concurrent resume must never be replaced.
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            Path(temporary).unlink(missing_ok=True)
