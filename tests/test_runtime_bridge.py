"""Bridge installation must not gate the independent native transports."""
import json
import sys
import pytest
from briefloop.runtime_bridge import RuntimeBridge


def test_missing_node_keeps_native_discovery_available(tmp_path, monkeypatch):
    from briefloop import host_bins
    monkeypatch.setenv('BRIEFLOOP_NODE', str(tmp_path/'missing-node'))
    monkeypatch.setenv('PATH', str(tmp_path))
    monkeypatch.setattr(host_bins, 'EXTRA_DIRS', ())
    for name in ('codex', 'opencode'):
        binary = tmp_path/name
        binary.write_text('#!/bin/sh\necho native-fixture-1\n')
        binary.chmod(0o755)
    bridge = RuntimeBridge()
    try:
        result = bridge.discover()
        native = {row['id']: row for row in result['runtimes']}
        for name in ('codex', 'opencode'):
            assert native[name]['available'] is True
            assert native[name]['path'] == str(tmp_path/name)
            assert native[name]['version'] == 'native-fixture-1'
        assert bridge.process is None
        with pytest.raises(RuntimeError, match='BRIEFLOOP_NODE'):
            bridge.call('start', {'runtime_id': 'claude'})
    finally:
        bridge.close()


@pytest.mark.parametrize('setting', ['constructor', 'environment'])
def test_custom_node_binary_is_used_for_bridge_requests(tmp_path, monkeypatch, setting):
    binary = tmp_path/'custom-node'
    binary.write_text('#!'+sys.executable+'\nimport json,sys\nfor line in sys.stdin:\n request=json.loads(line)\n print(json.dumps({"id":request["id"],"result":{"custom_node":True}}),flush=True)\n')
    binary.chmod(0o755)
    monkeypatch.setenv('BRIEFLOOP_NODE', str(binary) if setting == 'environment' else str(tmp_path/'missing-node'))
    bridge = RuntimeBridge(node_binary=str(binary)) if setting == 'constructor' else RuntimeBridge()
    try:
        assert bridge.call('ping') == {'custom_node': True}
    finally:
        bridge.close()
