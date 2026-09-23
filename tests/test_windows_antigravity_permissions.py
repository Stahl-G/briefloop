"""Installed-wheel native ACL smoke for the Antigravity settings writer."""
import os

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows DACL only')
def test_permission_settings_replacement_has_private_acl(tmp_path, monkeypatch):
    from briefloop import runtime_permissions
    from briefloop.connectors.windows_acl import verify_private

    path = tmp_path / 'settings.json'
    path.write_text('{"auth":{"fixture":"synthetic"},"permissions":{}}')
    monkeypatch.setattr(runtime_permissions, 'antigravity_settings', lambda: path)
    revision = runtime_permissions.catalog('antigravity', tmp_path, None)['revision']
    assert runtime_permissions.change_antigravity({
        'revision': revision, 'operation': 'preset', 'preset': 'default',
    })['saved']
    verify_private(path)
