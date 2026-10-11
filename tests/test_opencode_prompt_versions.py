"""Versioned subagent parameters without changing the research contract."""
import pytest

from briefloop.chat_tools import chat_instructions
from briefloop.runtime import generation_prompt, runtime_instruction
from briefloop.store import Store


@pytest.mark.parametrize('backend', ['codex', 'briefloop-native'])
def test_other_engines_do_not_probe_or_adopt_opencode_dialect(tmp_path, monkeypatch, backend):
    def forbidden():
        raise AssertionError('Other engines must not depend on installed OpenCode')
    monkeypatch.setattr('briefloop.opencode_version.installed_major', forbidden)
    store = Store(tmp_path / 'workspace')
    source = store.add_source('Fixture source', 'Read-only fixture.')
    run = store.create_run({'title': 'Fixture report', 'objective': 'Read material', 'allow_web': False}, [source['id']])
    folder = store.root / 'jobs' / 'prompt'; folder.mkdir()
    generation = generation_prompt(store, run, folder, backend=backend)
    stage = runtime_instruction({'model': 'fixture/tiny'}, backend=backend)
    chat = chat_instructions(store, {'backend': backend, 'model': 'fixture/tiny'}, backend=backend)
    assert '<subagent sessionID=' not in generation + stage + chat
