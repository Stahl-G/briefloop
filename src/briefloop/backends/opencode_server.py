"""Opencode backend transport, managed by BriefLoop.

Spawns ``opencode serve`` as a child process (loopback only) and drives the
v1 message surface — the same surface the official ``opencode run --attach``
client uses:

* ``POST /session?directory=...`` with ``{title, agent, model, permission}``
* ``POST /session/{id}/prompt_async`` with ``{model, agent, system, parts}``
* ``GET /session/{id}/message`` for polling completion and tool parts
* ``POST /session/{id}/abort`` for cancellation
* ``GET /session/{id}/children`` for subagent sessions

Shapes verified against opencode 1.18.20 (spec embedded in ``GET /doc``):

* create model is a ModelRef ``{providerID, id, variant?}``; prompt model is
  ``{providerID, modelID}``. The v2 ``/api/session/*/prompt`` body has NO
  model field — per-prompt models sent there are silently ignored, so the
  session always carries the frozen model.
* permission is a ruleset ``[{permission, action, pattern}]`` (same as the
  CLI's non-interactive mode). Deny rules fail fast instead of hanging a
  turn; ``external_directory`` allow/deny pairs scope file access to the
  workspace (last matching rule wins).
* a turn spans many assistant messages; intermediate ones complete with
  ``finish='tool-calls'``. Only ``finish='stop'`` ends the turn.

Only the standard library is used.
"""
import base64
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from ..platform_support import OwnedProcess


EXPECTED_MAJOR = 1


class OpencodeError(RuntimeError):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status  # HTTP status when known; None for transport errors


def split_model(model):
    """'provider/model...' -> (providerID, rest). Provider/model namespaces are
    opaque opencode configuration; only the first '/' separates them."""
    if not isinstance(model, str) or '/' not in model.strip():
        raise ValueError('opencode 模型必须是 provider/model 形式，例如 opencode-go/gpt-5.6-luna')
    provider, _, rest = model.strip().partition('/')
    if not provider or not rest:
        raise ValueError('opencode 模型必须是 provider/model 形式，例如 opencode-go/gpt-5.6-luna')
    return provider, rest


def model_ref(model, variant=None):
    """Session-level ModelRef {providerID, id, variant?}."""
    provider, rest = split_model(model)
    ref = {'providerID': provider, 'id': rest}
    if variant:
        ref['variant'] = variant
    return ref


def prompt_model(model):
    """Per-prompt v1 model {providerID, modelID}."""
    provider, rest = split_model(model)
    return {'providerID': provider, 'modelID': rest}


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class OpencodeServerClient:
    def __init__(self, log_directory, *, port=0, password=None, timeout=20):
        root = Path(log_directory)
        root.mkdir(parents=True, exist_ok=True)
        from ..host_bins import SEARCH_HINT, find as _find_host_bin
        executable = _find_host_bin('opencode')
        if not executable:
            raise RuntimeError('未找到 Opencode CLI；'+SEARCH_HINT)
        self.executable = executable
        self.timeout = timeout
        self.port = port or _free_port()
        self.password = password or secrets.token_urlsafe(24)
        env = {**os.environ, 'OPENCODE_SERVER_PASSWORD': self.password}
        from ..agent_commands import opencode_shell
        self.shell = opencode_shell()
        if self.shell:
            env['SHELL'] = self.shell
        self._stderr = (root / 'opencode-serve.stderr.log').open('a')
        self.process = OwnedProcess(
            [executable, 'serve', '--port', str(self.port), '--hostname', '127.0.0.1'],
            stdout=subprocess.DEVNULL, stderr=self._stderr, env=env, start_new_session=True)
        self._lock = threading.Lock()
        try:
            self.version = self._wait_ready()
        except BaseException:
            self.close()
            raise

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.port}'

    def _headers(self):
        token = base64.b64encode(f'opencode:{self.password}'.encode()).decode()
        return {'Content-Type': 'application/json', 'Authorization': 'Basic ' + token}

    def _request(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data,
                                         headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise OpencodeError(f'opencode {method} {path} 失败(HTTP {exc.code}): {exc.read()[:300]!r}',
                                  status=exc.code)
        except (urllib.error.URLError, OSError) as exc:
            raise OpencodeError(f'opencode 服务不可达: {exc}')
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise OpencodeError(f'opencode 返回非 JSON: {raw[:200]!r}')

    def _wait_ready(self):
        deadline = time.monotonic() + 25
        last = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('opencode serve 已退出')
            try:
                # /global/health requires auth once a server password is set.
                request = urllib.request.Request(self.base_url + '/global/health',
                                                 headers=self._headers())
                with urllib.request.urlopen(request, timeout=2) as response:
                    info = json.loads(response.read())
                if info.get('healthy'):
                    version = str(info.get('version', ''))
                    if not version.startswith(str(EXPECTED_MAJOR) + '.'):
                        raise RuntimeError(f'opencode 主版本 {version} 未验证，仅支持 1.x')
                    return version
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = exc
            time.sleep(.3)
        raise RuntimeError(f'opencode serve 未就绪: {last}')

    # -- v1 session surface -------------------------------------------------

    def create_session(self, title, *, agent='build', model=None, permission=None, directory=None, require_permissions=False):
        body = {'title': title}
        if agent:
            body['agent'] = agent
        if model:
            body['model'] = model if isinstance(model, dict) else model_ref(model)
        # Directory binds the session cwd; verified via ?directory= on 1.18.20.
        path = '/session'
        if directory is not None:
            path += '?directory=' + urllib.parse.quote(str(directory))
        if permission is not None:
            # Best effort: accepted by 1.18.20. Only schema rejections fall
            # back to a bare session; auth/network failures must surface.
            # The caller is told via `_permission_dropped` so it can record it.
            try:
                return self._request('POST', path, {**body, 'permission': permission})
            except OpencodeError as exc:
                if require_permissions or exc.status not in (400, 422):
                    raise
                bare = self._request('POST', path, body)
                bare['_permission_dropped'] = True
                return bare
        return self._request('POST', path, body)

    @staticmethod
    def _session_path(session_id, operation, directory):
        path = f'/session/{session_id}/{operation}'
        if directory is not None:
            path += '?directory=' + urllib.parse.quote(str(directory), safe='')
        return path

    def prompt_async(self, session_id, text, *, model=None, agent='build', files=None, system=None, directory=None):
        parts=[{'type':'text','text':text}]
        for item in files or []:
            parts.append({'type':'file','mime':item['mime'],'filename':item.get('filename','image'),
                          'url':item['url']})
        body = {'parts': parts}
        # Native prompt-level system string, verified against 1.18.30 /doc.
        # A schema rejection must surface; never retry after dropping the contract.
        if system is not None:
            body['system'] = system
        if agent:
            body['agent'] = agent
        if model:
            body['model'] = model if isinstance(model, dict) else prompt_model(model)
        self._request('POST', self._session_path(session_id, 'prompt_async', directory), body)

    def messages(self, session_id, *, directory=None):
        return self._request('GET', self._session_path(session_id, 'message', directory))

    def abort(self, session_id, *, directory=None):
        return self._request('POST', self._session_path(session_id, 'abort', directory))

    def children(self, session_id, *, directory=None):
        return self._request('GET', self._session_path(session_id, 'children', directory))

    def paths(self,directory):
        return self._request('GET','/path?directory='+urllib.parse.quote(str(directory),safe=''))

    def providers(self, directory=None):
        """Provider catalog with models (for the model picker, not inference)."""
        return self._request('GET', '/config/providers' + ('?directory=' + urllib.parse.quote(str(directory), safe='') if directory else ''))

    def provider_settings(self):
        """Project only editable public fields; native config may contain secrets."""
        data=self._request('GET','/global/config')
        protocols={'@ai-sdk/openai-compatible':'chat-completions',
                   '@ai-sdk/openai':'responses','@ai-sdk/anthropic':'anthropic-messages'}
        result=[]
        for pid,value in (data.get('provider') or {}).items():
            if value.get('npm') not in protocols:continue
            base=(value.get('options') or {}).get('baseURL','')
            url=urllib.parse.urlsplit(base)
            # Legacy URLs containing credentials must never enter the webpage.
            if url.username or url.password or url.query or url.fragment:base=''
            for mid,model in (value.get('models') or {}).items():
                limits=model.get('limit') or {}
                result.append({'provider':pid,'name':value.get('name',pid),'model':mid,
                               'base_url':base,'protocol':protocols[value['npm']],
                               'context_limit':limits.get('context'),'output_limit':limits.get('output'),
                               'supports_images':model.get('attachment'),'runtime':'opencode'})
        return result

    def probe_provider_catalog(self, provider):
        """Read directory only; inference/tool success is tested separately."""
        configs=self.provider_settings()
        config=next((c for c in configs if c['provider']==provider),None)
        if config is None:raise ValueError('请先保存 Provider')
        base=config['base_url']
        if not base:raise ValueError('请先保存不含凭据的 API Base URL')
        # Native OpenCode credentials stay in memory and never enter results.
        auth_path=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'opencode/auth.json'
        try:auth=json.loads(auth_path.read_text()).get(provider,{})
        except (OSError,ValueError):auth={}
        key=auth.get('key','') if auth.get('type')=='api' else ''
        headers={'Accept':'application/json'}
        if config['protocol']=='anthropic-messages':
            headers['anthropic-version']='2023-06-01'
            if key:headers['x-api-key']=key
        elif key:headers['Authorization']='Bearer '+key
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        catalog_url=base.rstrip('/')+'/models'
        if config['protocol']=='anthropic-messages' and urllib.parse.urlsplit(base).hostname=='api.deepseek.com':
            catalog_url='https://api.deepseek.com/models'
            headers={'Accept':'application/json',**({'Authorization':'Bearer '+key} if key else {})}
        request=urllib.request.Request(catalog_url,headers=headers)
        context=ssl.create_default_context()
        trust=ssl.get_default_verify_paths()
        if not trust.cafile and not trust.capath and not os.environ.get('SSL_CERT_FILE') and Path('/etc/ssl/cert.pem').is_file():
            context.load_verify_locations('/etc/ssl/cert.pem')
        result={'kind':'catalog','inference_tested':False,'tools_tested':False}
        try:
            with urllib.request.build_opener(NoRedirect,urllib.request.HTTPSHandler(context=context)).open(request,timeout=12) as response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('模型目录响应过大')
                data=json.loads(raw)
            models=sorted({m['id'] for m in data.get('data',[]) if isinstance(m,dict) and isinstance(m.get('id'),str) and len(m['id'])<=200})
            return {**result,'status':'reachable','models':models,'credential_sent':bool(key),'authenticated':'unknown'}
        except urllib.error.HTTPError as exc:
            kinds={401:'auth_failed',402:'insufficient_balance',403:'forbidden',404:'catalog_unavailable',429:'rate_limited'}
            return {**result,'status':kinds.get(exc.code,'upstream_unavailable' if exc.code>=500 else 'http_error'),'http_status':exc.code,'models':[]}
        except (urllib.error.URLError,OSError):
            return {**result,'status':'connection_failed','models':[]}
        except (ValueError,TypeError,AttributeError):
            return {**result,'status':'invalid_catalog','models':[]}

    def configure_provider(self, directory, provider, model, base_url, api_key=None, supports_images=None, protocol="chat-completions", name=None, context_limit=None, output_limit=None):
        """Use native configuration/auth APIs; never return credentials or config."""
        query = '?directory=' + urllib.parse.quote(str(directory), safe='')
        packages={'chat-completions':'@ai-sdk/openai-compatible',
                  'responses':'@ai-sdk/openai','anthropic-messages':'@ai-sdk/anthropic'}
        if protocol not in packages:raise ValueError('不支持的 API 协议')
        model_config={'name':model}
        limits={k:v for k,v in [('context',context_limit),('output',output_limit)] if v is not None}
        for value in limits.values():
            if type(value) is not int or value<1:raise ValueError('Token 上限必须是正整数，未知时留空')
        if limits:model_config['limit']=limits
        if supports_images is not None:
            model_config.update(attachment=supports_images,modalities={'input':['text','image'] if supports_images else ['text'],'output':['text']})
        try:
            self._request('PATCH', '/global/config', {'provider': {provider: {
                'npm': packages[protocol],
                'name': name or provider,
                'options': {'baseURL': base_url},
                'models': {model: model_config}
            }}})
            if api_key:
                self._request('PUT', '/auth/' + urllib.parse.quote(provider, safe=''),
                              {'type': 'api', 'key': api_key})
            self._request('POST', '/instance/dispose' + query)
        except OpencodeError as exc:
            # Native validation responses may echo request bodies containing keys.
            raise ValueError('Opencode 配置未全部完成，请重试保存；HTTP ' + str(exc.status or '连接失败')) from None
        return {'provider': provider, 'model': provider + '/' + model,
                'base_url': base_url, 'key_saved': bool(api_key),'supports_images':supports_images,
                'protocol':protocol,'runtime':'opencode','context_limit':context_limit,'output_limit':output_limit}

    def close(self):
        self.process.close_tree(timeout=8)
        try:
            self._stderr.close()
        except (OSError, ValueError):
            pass
