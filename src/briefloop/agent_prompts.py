"""System prompts for BriefLoop-owned engine sessions.

A system prompt is assembled from separately managed layers: the shared
baseline (core), exactly one role and exactly one mode. Tool guidance is
appended by the engine from the tools it actually registered, and the frozen
task (packet, IDs, schema) travels in the task message, never in the system
prompt. External hosts keep their own personas; these layers are only sent to
the native engine.
"""
import hashlib
from importlib import resources

ROLES = ('reviewer', 'evaluator', 'maintainer', 'proposer', 'scout')
MODES = ('background',)


def _asset(name):
    return resources.files('briefloop').joinpath('prompt_assets', name).read_text(encoding='utf-8').strip()


def system_prompt(role, mode='background'):
    if role not in ROLES:
        raise ValueError(f'unknown agent role: {role}')
    if mode not in MODES:
        raise ValueError(f'unknown agent mode: {mode}')
    text = '\n\n'.join(_asset(name) for name in ('core.zh.md', f'role.{role}.zh.md', f'mode.{mode}.zh.md'))
    return {'text': text, 'version': hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}
