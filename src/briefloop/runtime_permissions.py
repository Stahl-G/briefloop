"""User-facing native permission controls; never rewrite credentials or bypass policy."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from .backends import BACKEND_LABELS, WORKSPACE_SCOPES, validate_backend

PRESETS = {
    'default': {'toolPermission':'request-review','allowNonWorkspaceAccess':False},
    'full-machine': {'toolPermission':'request-review','allowNonWorkspaceAccess':True},
    'turbo': {'toolPermission':'always-proceed','allowNonWorkspaceAccess':True},
}

def preset_for(data):
    defaults=PRESETS['default']
    return next((name for name,values in PRESETS.items() if all(data.get(k,defaults[k])==v for k,v in values.items())), 'custom')

_lock = threading.RLock()
ACP = {'kimi','hermes','reasonix','codebuddy','kilo','kiro','vibe','deepseek-harness','mimo'}


def antigravity_settings():
    return Path.home()/'.gemini/antigravity-cli/settings.json'


def _read():
    path=antigravity_settings()
    if path.is_symlink():raise ValueError('权限配置是符号链接，请在宿主中管理')
    raw=path.read_bytes() if path.exists() else b'{}'
    if len(raw)>2_000_000:raise ValueError('宿主配置过大')
    data=json.loads(raw)
    if not isinstance(data,dict):raise ValueError('宿主配置格式不正确')
    permissions=data.get('permissions',{})
    if not isinstance(permissions,dict):raise ValueError('宿主权限配置格式不正确')
    for key in ('allow','ask','deny'):
        if key in permissions and (not isinstance(permissions[key],list) or not all(isinstance(v,str) for v in permissions[key])):
            raise ValueError('宿主权限列表格式不正确')
    return path,raw,data,permissions


def permission_digest():
    with _lock:
        _,_,data,permissions=_read()
        policy={'permissions':permissions,'enableTerminalSandbox':data.get('enableTerminalSandbox',False),**{k:data.get(k,v) for k,v in PRESETS['default'].items()}}
        return hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()


def _modes(value):
    """Keep the runtime's names, order and descriptions, never infer synonyms."""
    result=[]; seen=set()
    for row in value if isinstance(value,list) else []:
        if not isinstance(row,dict):continue
        mode=row.get('id')
        if not isinstance(mode,str) or not mode or len(mode)>100 or mode in seen:continue
        seen.add(mode)
        item={'id':mode,'name':row.get('name') if isinstance(row.get('name'),str) else mode}
        for key in ('description','native_name','disabled_reason'):
            if isinstance(row.get(key),str):item[key]=row[key]
        if row.get('disabled'):item['disabled']=True
        result.append(item)
    return result


def catalog(backend,workspace,bridge,model=None):
    backend=validate_backend(backend)
    result={'backend':backend,'workspace':str(workspace),'modes':[], 'default_mode':'native',
            'auto_available':False, 'interactive':backend in ACP-{'mimo'} or backend in ('claude','pi')}
    if backend in ('opencode','briefloop-native'):
        # OpenCode uses permission rules, not this pair of named modes. Native's
        # business-tool scopes are BriefLoop's own contract. Expose them honestly.
        result.update(kind='native',default_mode=WORKSPACE_SCOPES[0],default_source='adapter',
                      source={'kind':'adapter','label':'BriefLoop 执行范围'},
                      modes=[{'id':mode,'name':mode} for mode in WORKSPACE_SCOPES],
                      note=('OpenCode 提供逐工具权限规则，没有公开统一的权限模式目录。以下是 BriefLoop 接入层支持的执行范围。'
                            if backend=='opencode' else '以下来自 BriefLoop 内置引擎的执行范围定义。')+' 下一回合生效。')
        return result

    result.update(kind='native' if backend=='codex' else 'host',
                  source={'kind':'unavailable','label':'未取得运行端权限目录'},
                  note='保留运行端公布的名称、顺序与说明。更改用于下一回合。')
    if backend not in ('codex','zcode'):
        result['inherit_mode']={'id':'native','name':'沿用运行端设置','description':'BriefLoop 不覆盖运行端的权限模式。'}
    if backend=='antigravity':
        with _lock:
            path,raw,data,permissions=_read()
            rows=[{'decision':decision,'rule':rule} for decision in ('allow','ask','deny') for rule in permissions.get(decision,[]) if isinstance(rule,str)]
            result.update(rules=rows,revision=hashlib.sha256(raw).hexdigest(),config_path=str(path))
    try:
        if bridge is None:raise RuntimeError('运行端查询通道不可用')
        params={'runtime_id':backend,'cwd':str(workspace)}
        if model:params['model']=model
        found=bridge.call('permission_options',params,timeout=30)
        result['modes']=_modes(found.get('modes'))
        result['source']=found.get('source') if isinstance(found.get('source'),dict) else {'kind':'runtime','label':BACKEND_LABELS[backend]}
        for key in ('note','diagnostic','refreshed_at','native_default_mode','current_mode','model_diagnostic'):
            if isinstance(found.get(key),str):result[key]=found[key]
        if isinstance(found.get('default_source'),(str,dict)):result['default_source']=found['default_source']
        if isinstance(found.get('model_supports_auto'),bool):result['model_supports_auto']=found['model_supports_auto']
        if backend=='codex':
            for mode in result['modes']:
                if mode['id'] not in WORKSPACE_SCOPES:
                    mode.update(disabled=True,disabled_reason='BriefLoop 接入层尚未支持此执行范围。')
            result.update(default_mode=WORKSPACE_SCOPES[0],default_source='adapter')
        elif backend=='pi':
            result['kind']='tools'
            # Pi's original/default tool set is already the first adapter row.
            if any(mode['id']=='native' for mode in result['modes']):result.pop('inherit_mode',None)
        allowed={mode['id'] for mode in result['modes'] if not mode.get('disabled')}
        if found.get('default_mode') in allowed:
            result['default_mode']=found['default_mode']
            result['auto_available']=bool(found.get('auto_available'))
        if not result['modes'] and not result.get('diagnostic'):
            result['diagnostic']='运行端未返回可选择的权限模式；未添加预设选项。'
    except (ValueError,RuntimeError,OSError,TimeoutError) as e:
        result['diagnostic']='未能读取运行端权限：'+str(e)
    return result


def _protect_temp_file(fd, name, path):
    # Host settings may contain credentials. Protect the replacement before
    # serializing them; chmod/fchmod do not set a private Windows DACL.
    if os.name == 'nt':
        from .connectors.windows_acl import protect_private
        protect_private(name)
    else:
        os.fchmod(fd, 0o600)


def change_antigravity(body):
    """Patch only the exact permission rule explicitly submitted by the user."""
    with _lock:
        path,raw,data,permissions=_read()
        if body.get('revision')!=hashlib.sha256(raw).hexdigest():raise ValueError('宿主配置已变化，请刷新权限面板后重试')
        if body.get('operation')=='preset':
            preset=body.get('preset')
            if preset not in PRESETS:raise ValueError('无效权限预设')
            data={**data,**PRESETS[preset]}
        else:
            decision=body.get('decision');rule=body.get('rule');operation=body.get('operation')
            if decision not in ('allow','ask','deny') or operation not in ('add','remove'):raise ValueError('无效权限操作')
            if not isinstance(rule,str) or not rule:raise ValueError('请输入有效的原生权限规则')
            if operation=='add' and not re.fullmatch(r'(read_file|write_file|command|read_url|execute_url|mcp)\([^\r\n()\x00]{1,1000}\)',rule):raise ValueError('请输入有效的原生权限规则')
            values=permissions.get(decision,[])
            if not isinstance(values,list) or not all(isinstance(v,str) for v in values):raise ValueError('原生权限列表格式不正确')
            if operation=='add':values=[*values,*([] if rule in values else [rule])]
            else:values=[v for v in values if v!=rule]
            permissions={**permissions,decision:values};data={**data,'permissions':permissions}
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.briefloop-permissions-',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                _protect_temp_file(f.fileno(),name,path)
                json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            if path.is_symlink() or (path.read_bytes() if path.exists() else b'{}')!=raw:raise ValueError('宿主配置被同时修改，请刷新后重试')
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    return {'saved':True}


def validate_options(backend,options):
    if options is None:options={}
    if not isinstance(options,dict) or set(options)-{'mode'}:raise ValueError('无效宿主权限选项')
    # Supported modes arrive from the current native runtime. An omitted choice
    # stays omitted so the bridge can select Auto only if it is advertised.
    if backend in ACP|{'claude'} and not options:return {}
    mode=options.get('mode','native')
    if backend=='zcode' and mode=='native':mode='build'
    if not isinstance(mode,str) or not mode or len(mode)>100:raise ValueError('无效权限模式')
    allowed={'pi':{'native','read','none'},'antigravity':{'native','accept-edits','plan'}}
    # The bridge checks live CLI/ACP capability before passing a mode. Retain
    # only syntax/policy validation here instead of a second catalog snapshot.
    if backend in ('claude','zcode'):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,99}',mode) or mode=='bypassPermissions' or (backend=='claude' and mode=='yolo'):
            raise ValueError('此接入不支持跳过权限的模式')
        return {'mode':mode}
    if backend in allowed and mode not in allowed[backend]:raise ValueError('此宿主不支持该权限模式')
    if backend not in allowed and backend not in ACP and mode!='native':raise ValueError('此宿主不支持该权限模式')
    return {'mode':mode}
