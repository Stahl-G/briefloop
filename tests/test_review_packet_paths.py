"""Portable manifest paths must retain strict packet integrity checks."""
import json

import pytest

from briefloop.review import _packet, build_packet, sha
from briefloop.store import Store


@pytest.fixture
def packet_fixture(tmp_path):
    store=Store(tmp_path/'中文 工作区')
    source=store.add_source('Synthetic source','Revenue: 12 million USD.')
    run=store.create_run({'title':'Synthetic report','objective':'Explain revenue'},[source['id']])
    brief=store.publish(run['id'],{'title':'Synthetic report','markdown':'Revenue: 12 million USD.'})
    folder=store.root/'jobs'/'review-path-test'
    fingerprint,files=build_packet(store,brief['id'],folder)
    packet=folder/'packet'
    review={'version_id':brief['id'],'fingerprint':fingerprint,
            'data':{'packet_path':str(packet.relative_to(store.root)),'files':files}}
    return store,review,packet


def test_nested_packet_manifest_round_trips_on_native_platform(packet_fixture):
    store,review,packet=packet_fixture
    files=review['data']['files']
    assert 'history/executions.json' in files
    assert any(name.startswith('sources/') for name in files)
    assert all('\\' not in name for name in files)
    path,target,bound=_packet(store,review)
    assert path==packet
    assert target['version_id']==review['version_id']
    assert bound=={name:digest for name,digest in files.items() if name!='index.json'}


@pytest.mark.parametrize('change',['content','missing','extra'])
def test_nested_packet_tampering_is_still_rejected(packet_fixture,change):
    store,review,packet=packet_fixture
    history=packet/'history'/'executions.json'
    if change=='content':history.write_text('[] changed',encoding='utf-8')
    elif change=='missing':history.unlink()
    else:(packet/'history'/'extra.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='核查包文件'):
        _packet(store,review)


def test_index_tampering_cannot_be_hidden_by_updating_its_file_hash(packet_fixture):
    store,review,packet=packet_fixture
    path=packet/'index.json'
    index=json.loads(path.read_text(encoding='utf-8'))
    index['files']['history/executions.json']='0'*64
    path.write_text(json.dumps(index),encoding='utf-8')
    review['data']['files']['index.json']=sha(path.read_bytes())
    with pytest.raises(ValueError,match='索引与输入指纹不一致'):
        _packet(store,review)


def test_manifest_path_outside_packet_is_still_rejected(packet_fixture):
    store,review,packet=packet_fixture
    outside=packet.parent/'outside.json';outside.write_bytes(b'{}')
    review['data']['files']['../outside.json']=sha(outside.read_bytes())
    with pytest.raises(ValueError,match='核查包文件已变化'):
        _packet(store,review)
