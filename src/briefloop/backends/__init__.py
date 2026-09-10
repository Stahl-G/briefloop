"""Pluggable agent backends: Codex CLI (Responses) and Opencode CLI (multi-model).

Codex remains the default so existing workspaces behave exactly as before.
A workspace picks ``agent_backend`` once (startup/setup); every job freezes it
in its payload, exactly like the model configuration. One task never switches
backends mid-flight; a backend change starts a new attempt instead of resuming
old child handles.
"""

BRIDGE_BACKENDS = ('claude','kimi','hermes','reasonix','mimo')
BACKENDS = ('codex', 'opencode', *BRIDGE_BACKENDS)

DEFAULT_BACKEND = 'codex'

# Shown to users and to the chat model; never infer a host's name from another host.
BACKEND_LABELS = {
    'codex': 'Codex CLI',
    'opencode': 'Opencode CLI',
    'claude': 'Claude Code',
    'kimi': 'Kimi CLI',
    'hermes': 'Hermes',
    'reasonix': 'DeepSeek Reasonix',
    'mimo': 'MiMo Code',
}

# Verified against opencode 1.18.20 (v1 message surface, same as the official
# `run --attach` client): queue prompt, abort, message polling, task tool.
# v1 has no in-flight steer — steering a live opencode turn is refused and the
# message stays queued. `questions` never hangs: sessions deny them at create.
CAPABILITIES = {
    'codex': frozenset({'steer', 'cancel', 'questions', 'subagents', 'native_search'}),
    'opencode': frozenset({'cancel', 'subagents', 'native_search'}),
}


def validate_backend(name):
    if name not in BACKENDS:
        raise ValueError('未接入的 agent_backend：'+str(name))
    return name


def supports(backend, capability):
    return capability in CAPABILITIES.get(validate_backend(backend),frozenset({'cancel'}))
