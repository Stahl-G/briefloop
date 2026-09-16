"""Pluggable agent backends: Codex CLI (Responses) and Opencode CLI (multi-model).

Codex remains the default so existing workspaces behave exactly as before.
A workspace picks ``agent_backend`` once (startup/setup); every job freezes it
in its payload, exactly like the model configuration. One task never switches
backends mid-flight; a backend change starts a new attempt instead of resuming
old child handles.
"""

BRIDGE_BACKENDS = ('claude','kimi','hermes','reasonix','mimo','codebuddy','kilo','kiro','vibe','deepseek-harness','antigravity','pi')
# BriefLoop's own embedded engine (pi SDK in-process); not an external CLI.
# Phase 1 runs only the restricted Reviewer: it has no tools to research, write
# or learn with, so it can be pinned to a review job but never be the main
# chain. Settings cannot select it and every other job kind is refused.
REVIEW_ONLY_BACKENDS = ('briefloop-native',)
BACKENDS = ('codex', 'opencode', 'briefloop-native', *BRIDGE_BACKENDS)

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
    'codebuddy': 'CodeBuddy Code',
    'kilo': 'Kilo',
    'kiro': 'Kiro CLI',
    'vibe': 'Mistral Vibe CLI',
    'deepseek-harness': 'DeepSeek Harness',
    'antigravity': 'Antigravity',
    'pi': 'Pi',
    'briefloop-native': 'BriefLoop 内置引擎',
}

# Verified against opencode 1.18.20 (v1 message surface, same as the official
# `run --attach` client): queue prompt, abort, message polling, task tool.
# v1 has no in-flight steer — steering a live opencode turn is refused and the
# message stays queued. `questions` never hangs: sessions deny them at create.
# `restricted_review`: the Reviewer reads only its fixed packet, with no shell,
# writes, network or delegation, and the controller admits its reply. Only a
# backend whose tool policy was verified for that contract may declare it; an
# OS read-only sandbox alone is not enough. Unlisted backends never claim it.
CAPABILITIES = {
    'codex': frozenset({'steer', 'cancel', 'questions', 'subagents', 'native_search'}),
    'opencode': frozenset({'cancel', 'subagents', 'native_search', 'restricted_review'}),
    # Reviewer isolation here is our own tool proxy (packet-only reads, no
    # built-in tools at all), verified in native-engine/engine.test.mjs and the
    # phase-1 acceptance, not delegated to a host's permission UI.
    'briefloop-native': frozenset({'cancel', 'restricted_review'}),
}


def validate_backend(name):
    if name not in BACKENDS:
        raise ValueError('未接入的 agent_backend：'+str(name))
    return name


def require_main_chain(name):
    """The backend a report, scoring, research or learning job will run on."""
    name = validate_backend(name)
    if name in REVIEW_ONLY_BACKENDS:
        raise ValueError(f'{BACKEND_LABELS[name]}目前只执行受限独立审阅，不能用于生成、评分、研究或学习；请改用其他执行后端。')
    return name


def supports(backend, capability):
    return capability in CAPABILITIES.get(validate_backend(backend),frozenset({'cancel'}))
