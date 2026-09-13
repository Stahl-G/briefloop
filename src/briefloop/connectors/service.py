"""Synchronous local-settings facade; SDK ownership stays in its async thread.

The internal call/read methods are not report grants. Hosts must not expose them
to Agents until they bind trusted run authorization, budgets and durable receipts.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import uuid

from .config import ConnectorError, LocalConfig, validate_config, validate_secrets


class ConnectorService:
    def __init__(self, workspace_path: str | Path):
        self._config = LocalConfig(workspace_path)
        self._lock = threading.RLock()
        self._owners = {}
        self._portal = None
        self._portal_context = None
        self._closed = False
        self._errors = {}
        self._generation = {}
        self._instance_id = str(uuid.uuid4())
        self._revoked_scopes = set()

    def _get(self, identifier):
        if self._closed:
            raise ConnectorError('连接器服务已关闭。', code='closed')
        try:
            return self._config.records[identifier]
        except (KeyError, TypeError):
            raise ConnectorError('未找到该连接器。', code='not_found') from None

    def _view(self, record):
        credential_type = record.get('credential_type')
        credential_error = None
        if credential_type is None:
            try:
                credential_type = self._credential_type(self._config.get_secrets(record))
            except (OSError, ValueError):
                # A broken legacy binding must not hide other connections. Keep
                # the record repairable without exposing file contents or paths.
                credential_type = 'unknown'
                credential_error = {'code': 'credentials_unreadable',
                                    'message': '本地连接凭据无法读取，请重新选择认证方式并填写凭据。'}
        owners = [owner for (identifier, _, _), owner in self._owners.items() if identifier == record['id']]
        live = next((owner for owner in owners if owner.state == 'connected'), None)
        failed = next((owner for owner in owners if owner.state == 'error'), None)
        error = credential_error or self._errors.get(record['id']) or (failed.error if failed else None)
        connecting = any(owner.state == 'connecting' for owner in owners)
        state = 'error' if error else 'connected' if live else 'connecting' if connecting else 'disconnected' if record['enabled'] else 'disabled'
        return copy.deepcopy({**record['config'], 'id': record['id'], 'revision': record['revision'],
                              'enabled': record['enabled'], 'has_credentials': credential_type != 'none',
                              'credential_type': credential_type,
                              'env_names': record['env_names'], 'state': state, 'protocol': live.protocol if live else None,
                              'last_test': record.get('last_test'), 'error': error,
                              'warnings': live.warnings if live else (record.get('last_test') or {}).get('warnings', []),
                              'capabilities': live.capabilities if live else {'tools': [], 'resources': [], 'resource_templates': []}})

    def list(self):
        with self._lock:
            return [self._view(record) for record in self._config.records.values()]

    def status(self, connector_id):
        with self._lock:
            return self._view(self._get(connector_id))

    @staticmethod
    def _credential_type(values):
        if values.get('authorization_header'):return 'authorization'
        if values['bearer_token']:return 'bearer'
        return 'env' if values['env'] else 'none'

    def save(self, config, *, connector_id=None, secrets=None):
        validated = validate_config(config)
        if secrets is not None:
            secrets = validate_secrets(secrets)
        with self._lock:
            if self._closed:
                raise ConnectorError('连接器服务已关闭。', code='closed')
            existing = self._get(connector_id) if connector_id is not None else None
            identifier = existing['id'] if existing else str(uuid.uuid4())
            values = secrets if secrets is not None else self._config.get_secrets(existing) if existing else validate_secrets(None)
            if validated['transport'] == 'http' and values['env']:
                raise ConnectorError('HTTP 连接器请使用令牌凭据；env 仅适用于 stdio。')
            if validated['transport'] == 'stdio' and (values['bearer_token'] or values.get('authorization_header')):
                raise ConnectorError('stdio 连接器请通过 env 提供凭据。')
            if existing:
                self._generation[connector_id] = self._generation.get(connector_id, 0) + 1
                self._stop_owners(connector_id)
            binding = self._config.new_binding(values)
            record = {'id': identifier, 'config': validated, 'revision': existing['revision'] + 1 if existing else 1,
                      'enabled': False, 'credential_binding': binding,
                      'credential_type': self._credential_type(values),
                      'has_credentials': bool(values['bearer_token'] or values.get('authorization_header') or values['env']), 'env_names': sorted(values['env']),
                      'last_test': None}
            self._config.records[identifier] = record
            self._config.persist()
            if existing:
                self._config.credential_path(existing['credential_binding']).unlink(missing_ok=True)
            self._errors.pop(identifier, None)
            return self._view(record)

    def _ensure_portal(self):
        if self._portal is None:
            try:
                from anyio.from_thread import start_blocking_portal
                from .runtime import Owner
            except ImportError:
                raise ConnectorError('MCP 运行依赖尚未安装，请更新 BriefLoop 安装包。', code='sdk_unavailable') from None
            self._portal_context = start_blocking_portal(backend='asyncio', name='briefloop-connectors')
            self._portal = self._portal_context.__enter__()

    def _start(self, record, scope, generation):
        with self._lock:
            current = self._get(record['id'])
            if scope in self._revoked_scopes:
                raise ConnectorError('本轮连接授权已撤销。', code='revoked')
            if current['revision'] != record['revision'] or self._generation.get(record['id'], 0) != generation:
                raise ConnectorError('连接配置或启用状态已改变。', code='cancelled')
            self._ensure_portal()
            from .runtime import Owner
            key = (record['id'], record['revision'], scope)
            owner = self._owners.get(key)
            if owner is None:
                if len(self._owners) >= 32:
                    raise ConnectorError('连接器并行连接已达上限。', code='busy')
                owner = Owner(record['config'], self._config.get_secrets(record), scope)
                self._owners[key] = owner
                self._portal.start_task_soon(owner.run)
            elif owner.state not in ('connected', 'connecting') or owner.stopping:
                raise ConnectorError('该作用域的连接已终止；请重新测试或启用。', code='disconnected')
        return owner.ready.result(timeout=record['config']['timeout_seconds'] + 10)

    def _stop_owners(self, identifier=None, scope=None):
        for key, owner in list(self._owners.items()):
            if (identifier is None or key[0] == identifier) and (scope is None or key[2] == scope):
                self._portal.call(owner.stop)
                self._owners.pop(key, None)

    def test(self, connector_id):
        with self._lock:
            record = copy.deepcopy(self._get(connector_id))
            generation = self._generation.get(connector_id, 0)
        started = time.monotonic()
        preview = 'preview:' + str(uuid.uuid4())
        result = {'id': connector_id, 'ok': False, 'checked_at': datetime.now(timezone.utc).isoformat(),
                  'protocol': None, 'capabilities': {'tools': [], 'resources': [], 'resource_templates': []}, 'error': None, 'warnings': []}
        try:
            owner = self._start(record, preview, generation)
            result.update(ok=True, protocol=owner.protocol, capabilities=copy.deepcopy(owner.capabilities),
                          warnings=copy.deepcopy(owner.warnings))
        except ConnectorError as exc:
            result['error'] = {'code': exc.code, 'message': str(exc)}
        finally:
            with self._lock:
                self._stop_owners(connector_id, preview)
        result['duration_seconds'] = round(time.monotonic() - started, 3)
        with self._lock:
            current = self._config.records.get(connector_id)
            if current and current['revision'] == record['revision'] and not self._closed:
                current['last_test'] = copy.deepcopy(result)
                self._config.persist()
        return result

    def enable(self, connector_id):
        with self._lock:
            record = copy.deepcopy(self._get(connector_id))
            self._stop_owners(connector_id)
            generation = self._generation.get(connector_id, 0) + 1
            self._generation[connector_id] = generation
            self._config.records[connector_id]['enabled'] = False
            self._config.persist()
        error = None
        try:
            owner = self._start(record, 'settings', generation)
        except ConnectorError as exc:
            error = {'code': exc.code, 'message': str(exc)}
        with self._lock:
            current = self._get(connector_id)
            if self._generation.get(connector_id) == generation:
                current['enabled'] = error is None
                if error:
                    self._errors[connector_id] = error
                else:
                    self._errors.pop(connector_id, None)
                self._config.persist()
            return self._view(current)

    def disable(self, connector_id):
        with self._lock:
            record = self._get(connector_id)
            self._generation[connector_id] = self._generation.get(connector_id, 0) + 1
            record['enabled'] = False
            self._config.persist()
            self._stop_owners(connector_id)
            self._errors.pop(connector_id, None)
            return self._view(record)

    def delete(self, connector_id):
        with self._lock:
            record = self._get(connector_id)
            self._generation[connector_id] = self._generation.get(connector_id, 0) + 1
            self._stop_owners(connector_id)
            del self._config.records[connector_id]
            self._config.persist()
            self._config.credential_path(record['credential_binding']).unlink(missing_ok=True)
            self._errors.pop(connector_id, None)
            return {'id': connector_id, 'deleted': True}

    def _operation(self, connector_id, method, args, scope_id, expected_revision=None):
        if not isinstance(scope_id, str) or not scope_id.strip() or len(scope_id) > 200 or scope_id.startswith(('settings', 'preview:')):
            raise ConnectorError('调用需要独立的可信作用域。', code='invalid_scope')
        with self._lock:
            record = copy.deepcopy(self._get(connector_id))
            self._check_material_scope(connector_id, scope_id, expected_revision)
            generation = self._generation.get(connector_id, 0)
            if not record['enabled']:
                raise ConnectorError('连接器尚未启用。', code='disabled')
            if len(json.dumps(args, ensure_ascii=False).encode()) > record['config']['max_response_bytes']:
                raise ConnectorError('请求参数过大。', code='request_limit')
        owner = self._start(record, scope_id, generation)
        with self._lock:
            self._check_material_scope(connector_id, scope_id, expected_revision)
            if self._generation.get(connector_id, 0) != generation or not self._get(connector_id)['enabled']:
                raise ConnectorError('连接器已停用。', code='disabled')
            future = self._portal.call(owner.submit, method, args)
        # Never hold the config lock while waiting for network I/O: disable must win.
        result = future.result(timeout=record['config']['timeout_seconds'] + 10)
        return {**result, 'connection_epoch': f'{self._instance_id}:{generation}',
                'connector_revision': record['revision']}

    def call(self, connector_id, name, arguments, *, scope_id, expected_revision=None):
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise ConnectorError('工具调用需要名称及参数对象。')
        return self._operation(connector_id, 'call', (name, arguments), scope_id, expected_revision)

    def read(self, connector_id, uri, *, scope_id, expected_revision=None):
        if not isinstance(uri, str) or not uri or len(uri) > 8192:
            raise ConnectorError('资源 URI 无效。')
        return self._operation(connector_id, 'read', (uri,), scope_id, expected_revision)

    def _check_material_scope(self, connector_id, scope_id, expected_revision):
        record = self._get(connector_id)
        if scope_id in self._revoked_scopes:
            raise ConnectorError('本轮连接授权已撤销。', code='revoked')
        if expected_revision is not None and record['revision'] != expected_revision:
            raise ConnectorError('连接配置已改变，请重新选择本轮授权。', code='revision_changed')
        if not record['enabled']:
            raise ConnectorError('连接器尚未启用。', code='disabled')

    @contextmanager
    def material_admission(self, connector_id, *, scope_id, expected_revision, connection_epoch):
        """Serialize local source admission against disable/config changes; no network."""
        with self._lock:
            self._check_material_scope(connector_id, scope_id, expected_revision)
            if (scope_id != 'freeze' or connection_epoch is not None) and connection_epoch != f'{self._instance_id}:{self._generation.get(connector_id, 0)}':
                raise ConnectorError('该回执所属连接已停止，不能接纳迟到材料。', code='stale_receipt')
            yield

    def revoke_scope(self, scope_id):
        with self._lock:
            self._revoked_scopes.add(scope_id)
            self._stop_owners(scope=scope_id)

    def close_scope(self, scope_id):
        with self._lock:
            self._stop_owners(scope=scope_id)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._stop_owners()
            finally:
                if self._portal_context is not None:
                    self._portal_context.__exit__(None, None, None)
                    self._portal = self._portal_context = None
