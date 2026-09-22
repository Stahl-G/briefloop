"""Role/skill bindings are data; additional roles use the same learning path."""
import json
import re


def register_target(store, role_id, instruction):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',role_id) or not instruction.strip():
        raise ValueError('Provide a role ID and its execution instruction')
    roles=store.meta('additional_roles',{})
    roles[role_id]={'role_id':role_id,'instruction':instruction}
    store.set_meta('additional_roles',roles)
    store.update_settings(lambda settings: {'skill_targets': list(dict.fromkeys([*settings['skill_targets'],role_id]))})
    return roles[role_id]


def bind_context(store, skill):
    if not skill:return {}
    targets=skill.get('targets',store.settings()['skill_targets'])
    if isinstance(targets,str):targets=json.loads(targets)
    return {role:{'version':skill['id'],'instructions':skill['content']} for role in targets}
