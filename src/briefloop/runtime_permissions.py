"""User-facing native permission controls; never rewrite credentials or bypass policy."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from .backends import validate_backend

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


def catalog(backend,workspace,bridge):
    backend=validate_backend(backend)
    result={'backend':backend,'workspace':str(workspace),'modes':[], 'interactive':backend in ACP-{'mimo'} or backend in ('claude','pi')}
    if backend in ('codex','opencode','briefloop-native'):
        result.update(kind='native',modes=[{'id':'workspace-write','name':'读写工作区'},{'id':'read-only','name':'只读'}],note='选择用于下一回合；已发送的任务保持原权限。')
    elif backend=='pi':
        result.update(kind='tools',modes=[{'id':'native','name':'沿用 Pi 工具设置'},{'id':'read','name':'只启用读取工具'},{'id':'none','name':'关闭所有工具'}],note='读取工具模式关闭扩展，仅启用 read、grep、find、ls；它不是文件路径或网络沙箱。Pi 原生工具默认不逐次询问，扩展的确认请求可在这里回答。')
    elif backend=='claude':
        result.update(kind='host',modes=[{'id':'native','name':'沿用宿主设置，接收授权请求'},{'id':'manual','name':'需要时询问'},{'id':'acceptEdits','name':'自动允许文件编辑'},{'id':'dontAsk','name':'拒绝需要询问的操作'},{'id':'plan','name':'规划模式'}],note='使用 Claude 的原生权限模式。已有拒绝规则继续有效；规划模式不是操作系统级只读隔离。')
    elif backend=='zcode':
        result.update(kind='host',default_mode='build',modes=[{'id':'build','name':'构建模式（默认）'},{'id':'edit','name':'编辑模式'},{'id':'plan','name':'规划模式'},{'id':'yolo','name':'不再询问（yolo）'}],note='默认明确使用构建模式；只有选择“不再询问”才启用 yolo。下一回合生效。ZCode 无界面运行不提供逐项授权通道：被模式拦下的操作直接失败，规划模式也不是操作系统级只读隔离。')
    elif backend=='antigravity':
        with _lock:
            path,raw,data,permissions=_read()
            rows=[{'decision':decision,'rule':rule} for decision in ('allow','ask','deny') for rule in permissions.get(decision,[]) if isinstance(rule,str)]
            result.update(kind='rules',preset=preset_for(data),rules=rows,revision=hashlib.sha256(raw).hexdigest(),config_path=str(path),note='应用后对新任务生效，也会影响本机其他 Antigravity 会话。已有自定义规则保留。')
    else:
        result.update(kind='host',modes=[{'id':'native','name':'沿用宿主设置，逐项确认'}],note='宿主通过 ACP 发来的授权请求可在 BriefLoop 中批准或拒绝；模式名称和行为由宿主定义。')
        if backend=='mimo':result['note']='MiMo 使用宿主提供的运行模式，应用于下一回合。规划模式的文件权限由 MiMo 执行；当前 JSON 运行接口不能在 BriefLoop 逐项回答授权。需要交互批准时请在 MiMo 中配置，或选择可交互授权的宿主。'
        try:
            found=bridge.call('permission_options',{'runtime_id':backend,'cwd':str(workspace)},timeout=30)
            result['modes']+=found.get('modes',[])
        except (ValueError,RuntimeError,OSError,TimeoutError) as e:
            result['diagnostic']='未能读取宿主模式：'+str(e)
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
    if options is None:return {}
    if not isinstance(options,dict) or set(options)-{'mode'}:raise ValueError('无效宿主权限选项')
    mode=options.get('mode','native')
    if backend=='zcode' and mode=='native':mode='build'
    if not isinstance(mode,str) or not mode or len(mode)>100:raise ValueError('无效权限模式')
    allowed={'pi':{'native','read','none'},'claude':{'native','manual','acceptEdits','dontAsk','plan'},'antigravity':{'native'},'zcode':{'native','build','edit','plan','yolo'}}
    if backend in allowed and mode not in allowed[backend]:raise ValueError('此宿主不支持该权限模式')
    if backend not in allowed and backend not in ACP and mode!='native':raise ValueError('此宿主不支持该权限模式')
    return {'mode':mode}
