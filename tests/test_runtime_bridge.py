"""Bridge installation must not gate the independent native transports."""
import json
import sys
import os
import shutil
from pathlib import Path
import pytest
from briefloop.runtime_bridge import RuntimeBridge


def test_missing_node_keeps_native_discovery_available(tmp_path, monkeypatch):
    from briefloop import host_bins
    node=shutil.which('node')
    monkeypatch.setenv('BRIEFLOOP_NODE', str(tmp_path/'missing-node'))
    monkeypatch.setenv('PATH', str(tmp_path))
    monkeypatch.setattr(host_bins, 'EXTRA_DIRS', ())
    if os.name=='nt':
        if not node:pytest.skip('Node required for npm fixture')
        monkeypatch.delenv('APPDATA',raising=False)
        original_find=host_bins.find
        monkeypatch.setattr(host_bins,'find',lambda name,**kw:node if name=='node' else original_find(name,**kw))
        (tmp_path/'entry.cjs').write_text('console.log("native-fixture-1")',encoding='utf-8')
    for name in ('codex', 'opencode'):
        binary = tmp_path/(name+'.cmd' if os.name=='nt' else name)
        if os.name=='nt':
            binary.write_text('@echo off',encoding='utf-8')
            binary.with_suffix('').write_text('exec node "$basedir/entry.cjs" "$@"',encoding='utf-8')
        else:binary.write_text('#!/bin/sh\necho native-fixture-1\n')
        binary.chmod(0o755)
    bridge = RuntimeBridge()
    try:
        result = bridge.discover()
        native = {row['id']: row for row in result['runtimes']}
        for name in ('codex', 'opencode'):
            assert native[name]['available'] is True
            assert Path(native[name]['path']) == tmp_path/(name+'.cmd' if os.name=='nt' else name)
            assert native[name]['version']=='native-fixture-1'
        assert bridge.process is None
        with pytest.raises(RuntimeError, match='BRIEFLOOP_NODE'):
            bridge.call('start', {'runtime_id': 'claude'})
    finally:
        bridge.close()


@pytest.mark.parametrize('setting', ['constructor', 'environment'])
@pytest.mark.parametrize('electron', [None, '0', '1'])
def test_custom_node_binary_is_used_for_bridge_requests(tmp_path, monkeypatch, setting, electron):
    binary = tmp_path/'custom-node'
    binary.write_text('#!'+sys.executable+'\nimport json,sys,os\nfor line in sys.stdin:\n request=json.loads(line)\n print(json.dumps({"id":request["id"],"result":{"custom_node":True,"electron_flag":os.environ.get("ELECTRON_RUN_AS_NODE")}}),flush=True)\n')
    binary.chmod(0o755)
    if os.name=='nt':
        if not shutil.which('node'):pytest.skip('Node required for native Windows bridge fixture')
        binary.write_text('exec node "$basedir/entry.cjs" "$@"',encoding='utf-8')
        (tmp_path/'entry.cjs').write_text('require("node:readline").createInterface({input:process.stdin}).on("line",line=>console.log(JSON.stringify({id:JSON.parse(line).id,result:{custom_node:true,electron_flag:process.env.ELECTRON_RUN_AS_NODE??null}})));',encoding='utf-8')
        binary=tmp_path/'custom-node.cmd';binary.write_text('@echo off',encoding='utf-8')
    monkeypatch.setenv('BRIEFLOOP_NODE', str(binary) if setting == 'environment' else str(tmp_path/'missing-node'))
    monkeypatch.setenv('ELECTRON_RUN_AS_NODE', 'parent-sentinel')
    if electron is None:monkeypatch.delenv('BRIEFLOOP_NODE_IS_ELECTRON', raising=False)
    else:monkeypatch.setenv('BRIEFLOOP_NODE_IS_ELECTRON', electron)
    bridge = RuntimeBridge(node_binary=str(binary)) if setting == 'constructor' else RuntimeBridge()
    try:
        assert bridge.call('ping') == {'custom_node': True, 'electron_flag': '1' if electron=='1' else None}
        assert os.environ['ELECTRON_RUN_AS_NODE']=='parent-sentinel'
    finally:
        bridge.close()


def test_acp_adapters_are_selectable_after_installation(monkeypatch):
    from briefloop.backends import validate_backend
    from briefloop.models import Settings
    bridge=RuntimeBridge()
    rows=[{'id':name,'installed':True,'capabilities':{'chat':True},'protocol':'acp'} for name in ('kilo','kiro','vibe','deepseek-harness')]
    monkeypatch.setattr(bridge,'call',lambda *args,**kwargs:rows)
    for row in bridge.discover()['runtimes']:
        assert row['integrated'] and row['available']
        assert validate_backend(row['id'])==row['id']
        assert Settings(agent_backend=row['id']).agent_backend==row['id']
    rows[0]['installed']=False
    assert not bridge.discover()['runtimes'][0]['available']
