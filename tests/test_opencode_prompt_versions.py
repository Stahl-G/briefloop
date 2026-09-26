"""Versioned subagent parameters without changing the research contract."""
import json
import pytest

from briefloop.chat_tools import chat_instructions
from briefloop.runtime import COMMON_OPENCODE, generation_prompt, runtime_instruction
from briefloop.store import Store


@pytest.mark.parametrize('major', [1, 2, None])
def test_all_opencode_prompt_surfaces_use_installed_subagent_protocol(tmp_path, monkeypatch, major):
    monkeypatch.setattr('briefloop.opencode_version.installed_major', lambda: major)
    store = Store(tmp_path / 'workspace')
    source = store.add_source('Fixture source', 'Company A shipped twelve units.')
    run = store.create_run({'title': 'Fixture report', 'objective': 'Read the supplied source', 'allow_web': True}, [source['id']])
    folder = store.root / 'jobs' / 'prompt'; folder.mkdir()
    runtime = {'backend': 'opencode', 'model': 'fixture/tiny', 'variant': 'high'}
    generation = generation_prompt(store, {**run, 'search_provider': 'tavily'}, folder, backend='opencode')
    stage = runtime_instruction({'model': 'fixture/tiny', 'model_variant': 'high'}, backend='opencode')
    chat = chat_instructions(store, runtime, backend='opencode', allow_web=True)
    if major == 2:
        for prompt in (generation, stage, chat):
            assert all(word in prompt for word in ('subagent', 'agent', 'description', 'prompt', 'sessionID', 'completed'))
            assert 'task 工具' not in prompt and 'subagent_type' not in prompt and '<task id>' not in prompt
            assert 'model override' in prompt
        assert '实际 subagent 工具消息优先使用精简任务' in generation
        assert '<subagent sessionID="..." state="completed">' in generation
    else:
        assert COMMON_OPENCODE in generation
        assert '实际 task 工具消息优先使用精简任务' in generation
        assert 'subagent_type' in generation and '<task id>' in generation and 'ses_' in generation
        assert 'task 工具' in stage and 'task 工具' in chat
    # Only host dialect changes: the supplied source, frozen retrieval skill
    # and its Scout-only assignment still travel in the actual packet.
    packet = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
    assert packet['search_provider'] == 'tavily'
    assert packet['sources'][0]['id'] == source['id']
    assert packet['retrieval_skill']['target_roles'] == ['scout']
    assert '本轮冻结搜索源：Tavily。' in generation


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
    assert '本轮未允许联网，只处理已登记的材料' in generation
