"""Lightweight per-workspace basics the assistant collects on first contact.

These are conversation conveniences (how to address the user, default company
and role), not source-backed evidence; company facts stay in company_context.
"""
from .store import dump

FIELDS = ('name', 'organization', 'role', 'location', 'focus', 'report_types')
LABELS = {'name': '称呼', 'organization': '公司/组织', 'role': '岗位',
          'location': '城市', 'focus': '主要工作', 'report_types': '常做报告'}


def read(store):
    value = store.meta('workspace_profile') or {}
    if not isinstance(value, dict):
        return {}
    return {key: text for key, text in value.items() if key in FIELDS and text}


def update(store, fields):
    if not isinstance(fields, dict):
        raise ValueError('工作区基础设定必须是 JSON 对象')
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError('不支持的基础设定字段：' + ', '.join(sorted(unknown)))
    current = read(store)
    for key, value in fields.items():
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(LABELS.get(key, key) + '需要是文本')
        text = ' '.join(value.split())
        if len(text) > 200:
            raise ValueError((LABELS.get(key, key)) + '过长')
        if text:
            current[key] = text
        else:
            current.pop(key, None)
    store.set_meta('workspace_profile', current)
    return current


def prompt(store):
    profile = read(store)
    if not profile:
        return ('本工作区还没有基础设定。用户只是打招呼时可自然邀请介绍称呼、组织或工作；'
                '用户已给出具体任务时，优先完成任务。读者、目的与期间已明确就不再追问姓名、公司、岗位等无关资料，不把基础设定当开工门槛。'
                '只有缺失信息会实质改变报告时才简短询问；用户主动提供后用 workspace-action 的 profile_update 保存，允许跳过。')
    return '本工作区基础设定（用于称呼与默认上下文，不是证据来源）：' + dump(profile)
