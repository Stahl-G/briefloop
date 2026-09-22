"""Concurrent saves must not restore revoked background-learning consent."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
import threading

import pytest
from pydantic import ValidationError

from briefloop import learning_budget
from briefloop.server import make_server, _close_service
from briefloop.skills import register_target
from briefloop.store import Store


@contextmanager
def running_settings_server(root):
    server = make_server(root, port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    authority = f'127.0.0.1:{server.server_port}'

    def request(path, body=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        try:
            connection.request('GET' if body is None else 'POST', path,
                None if body is None else json.dumps(body),
                {'Host': authority, 'Origin': 'http://' + authority, **headers})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        status, session = request('/api/session')
        assert status == 200

        def post(body, **headers):
            return request('/api/settings', body,
                **{'X-BriefLoop-Token': session['token'], **headers})

        yield server, post
    finally:
        server.shutdown()
        thread.join(timeout=10)
        _close_service(server)


def test_concurrent_unrelated_save_cannot_restore_revoked_learning(tmp_path, monkeypatch):
    first_read = threading.Event()
    release_first = threading.Event()
    revoke_entered = threading.Event()
    revoke_finished = threading.Event()
    original_merge = learning_budget.apply_settings_change

    def pause_unrelated_save(current, body):
        if body.get('max_reports') == 3:
            assert current['auto_learn'] is True
            first_read.set()
            assert release_first.wait(10), 'Concurrent settings test did not release its first save'
        return original_merge(current, body)

    with running_settings_server(tmp_path / 'workspace') as (server, post):
        assert post({'auto_learn': True, 'confirm_learning_rounds': 1})[0] == 200
        assert learning_budget.automatic_allowed(server.store.settings())
        monkeypatch.setattr(learning_budget, 'apply_settings_change', pause_unrelated_save)
        original_post = server.RequestHandlerClass.do_POST

        def observe_revocation(handler):
            if handler.headers.get('X-Test-Revocation'):
                revoke_entered.set()
            return original_post(handler)

        monkeypatch.setattr(server.RequestHandlerClass, 'do_POST', observe_revocation)

        def revoke():
            try:
                return post({'auto_learn': False}, **{'X-Test-Revocation': '1'})
            finally:
                revoke_finished.set()

        with ThreadPoolExecutor(max_workers=2) as clients:
            unrelated = clients.submit(post, {'max_reports': 3})
            try:
                assert first_read.wait(10)
                revocation = clients.submit(revoke)
                assert revoke_entered.wait(10)
                # With the former read/merge/write split, revocation commits
                # here and the stale first save restores it. Atomic saves keep
                # the second request waiting until the first one is released.
                revoke_finished.wait(0.5)
            finally:
                release_first.set()
            assert unrelated.result(timeout=10)[0] == 200
            status, revoked = revocation.result(timeout=10)
            assert status == 200 and revoked['auto_learn'] is False

        saved = Store(server.store.root).settings()
        assert saved['max_reports'] == 3
        assert saved['auto_learn'] is False
        assert saved['auto_learn_authorized_rounds'] is None
        assert saved['auto_learn_authorized_plan'] is None
        assert not learning_budget.automatic_allowed(saved)


def test_settings_patches_preserve_registered_targets_and_explicit_model(tmp_path):
    from briefloop.chat_tools import workspace_action
    from briefloop.demo import create_demo

    with running_settings_server(tmp_path / 'workspace') as (server, post):
        store = server.store
        original_targets = store.settings()['skill_targets']
        register_target(store, 'review_tables', 'Check the units in each table.')
        register_target(store, 'review_tables', 'Check both units and totals.')
        status, saved = post({'model': 'gpt-5.6-luna', 'agent_backend': 'codex'})
        assert status == 200 and saved['model_selection_required'] is False
        workspace_action(store, {'action': 'company_config', 'enabled': False})
        create_demo(store)
        saved = store.settings()
        assert saved['skill_targets'] == [*original_targets, 'review_tables']
        assert store.meta('additional_roles')['review_tables']['instruction'] == 'Check both units and totals.'
        assert saved['company_context_enabled'] is False
        assert saved['model_selection_required'] is False
        assert store.runtime_config()['model'] == 'gpt-5.6-luna'
        assert saved['auto_learn'] is False


def test_concurrent_role_registration_keeps_both_instructions_and_revoked_consent(tmp_path, monkeypatch):
    first, second = Store(tmp_path), Store(tmp_path)
    first.update_settings({'auto_learn': True, 'confirm_learning_rounds': 1})
    first.update_settings({'auto_learn': False})
    original_targets = first.settings()['skill_targets']
    first_locked, second_entered, release_first = (threading.Event() for _ in range(3))
    first_tx, second_tx = first.tx, second.tx

    @contextmanager
    def hold_first_writer():
        with first_tx() as connection:
            first_locked.set()
            assert release_first.wait(10)
            yield connection

    @contextmanager
    def observe_second_writer():
        second_entered.set()
        with second_tx() as connection:
            yield connection

    monkeypatch.setattr(first, 'tx', hold_first_writer)
    monkeypatch.setattr(second, 'tx', observe_second_writer)
    with ThreadPoolExecutor(max_workers=2) as clients:
        units = clients.submit(register_target, first, 'units', 'Check units.')
        try:
            assert first_locked.wait(10)
            totals = clients.submit(register_target, second, 'totals', 'Check totals.')
            assert second_entered.wait(10)
            # A read outside the write transaction would already be stale here.
        finally:
            release_first.set()
        assert units.result(timeout=10)['instruction'] == 'Check units.'
        assert totals.result(timeout=10)['instruction'] == 'Check totals.'

    saved = Store(tmp_path)
    assert saved.meta('additional_roles') == {
        'units': {'role_id': 'units', 'instruction': 'Check units.'},
        'totals': {'role_id': 'totals', 'instruction': 'Check totals.'}}
    settings = saved.settings()
    assert settings['skill_targets'] == [*original_targets, 'units', 'totals']
    assert settings['auto_learn'] is False
    assert settings['auto_learn_authorized_rounds'] is None
    assert settings['auto_learn_authorized_plan'] is None


def test_role_registration_rolls_back_when_settings_validation_fails(tmp_path, monkeypatch):
    store = Store(tmp_path)
    register_target(store, 'units', 'Original instruction.')
    roles, settings = store.meta('additional_roles'), store.settings()
    merge = learning_budget.apply_settings_change

    def invalid_settings(current, body):
        return {**merge(current, body), 'k': 0}

    monkeypatch.setattr(learning_budget, 'apply_settings_change', invalid_settings)
    with pytest.raises(ValidationError):
        register_target(store, 'units', 'Must not be partially saved.')
    assert store.meta('additional_roles') == roles
    assert store.settings() == settings
