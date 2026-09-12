"""Workspace basics the assistant collects on first contact."""
from briefloop.chat_tools import chat_instructions, workspace_action
from briefloop.store import Store
from briefloop.workspace_profile import read, update


def test_profile_round_trips_through_workspace_action(tmp_path):
    store = Store(tmp_path)
    assert workspace_action(store, {'action': 'profile_read'}) == {}
    saved = workspace_action(store, {'action': 'profile_update', 'profile': {
        'name': '  Stahl ', 'organization': '智谱AI', 'role': '战略', 'report_types': '周报、月报'}})
    assert saved['name'] == 'Stahl'
    assert read(store)['organization'] == '智谱AI'
    assert store.snapshot()['profile']['role'] == '战略'


def test_profile_update_rejects_unknown_and_non_text(tmp_path):
    store = Store(tmp_path)
    try:
        update(store, {'nickname': 'x'})
        raise AssertionError('unknown field accepted')
    except ValueError as error:
        assert 'nickname' in str(error)
    try:
        update(store, {'name': 3})
        raise AssertionError('non-text accepted')
    except ValueError as error:
        assert '称呼' in str(error)


def test_empty_profile_asks_once_and_saved_profile_is_context(tmp_path):
    store = Store(tmp_path)
    runtime = {'model': 'gpt-5.6-luna', 'backend': 'codex', 'effort': 'high'}
    empty = chat_instructions(store, runtime)
    assert 'profile_update' in empty
    assert '本工作区还没有基础设定' in empty
    update(store, {'name': 'Stahl', 'organization': '智谱AI'})
    filled = chat_instructions(store, runtime)
    assert 'Stahl' in filled
    assert '本工作区还没有基础设定' not in filled
