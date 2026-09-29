"""Native artifact IO and boundaries; synthetic reviews only test the controller."""
from io import BytesIO
import json
import os
import threading
from zipfile import ZipFile

import pytest

from briefloop.audit_bundle import bundle_file, enqueue_bundle, generate_bundle, verify_bundle
from briefloop.platform_support import filesystem_path
from briefloop.release import enqueue_release, generate_release, release_file, safe_file, sha, validate_release
from briefloop.store import Store
from test_release import complete_release, reviewed_report


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native paths and junctions')


@pytest.fixture(params=[False, True], ids=['short', 'long-unicode'])
def case(tmp_path, request):
    name='中文正式交付-'
    if request.param:name+='w'*(224-len(str(tmp_path))-1-len(name))
    Store(tmp_path/name).update_settings({'model':'gpt-6-luna', 'reasoning_effort':'high', 'auto_learn':False})
    return reviewed_report(tmp_path/name)


def test_formal_word_audit_permissions_offline_paths_and_tamper_checks(case):
    store, source, brief, review_id=case
    release, job=complete_release(store, brief)
    manifest=validate_release(store, release)
    report=release_file(store, release['id'])
    assert sha(filesystem_path(report).read_bytes())==release['result']['sha256']
    assert manifest['review_id']==review_id and not str(report).startswith('\\\\?\\')
    for mode in ('original', 'metadata'):
        bundle=enqueue_bundle(store, release['id'], {source['id']:{'mode':mode, 'reason':'Explicit synthetic IO test'}})
        result=generate_bundle(store, bundle, threading.Event())
        store.update_job(bundle['id'], 'complete', result=result)
        path=bundle_file(store, bundle['id']);blob=filesystem_path(path).read_bytes()
        assert not str(path).startswith('\\\\?\\') and sha(blob)==result['sha256']
        for value in (path, str(path), blob, BytesIO(blob)):
            checked=verify_bundle(value)
            assert checked['valid'],checked['errors']
        with ZipFile(BytesIO(blob)) as archive:
            info=json.loads(archive.read('manifest.json'))
            assert info['release_id']==release['id'] and info['report_sha256']==release['result']['sha256']
            source_files={name for name in info['files'] if name.startswith('packet/sources/')}
            assert bool(source_files)==(mode=='original')
            if mode=='metadata':
                assert info['omissions'] and info['transformations']
                assert b'Unused private appendix line.' not in b''.join(archive.read(n) for n in archive.namelist())
        if len(str(store.root))==224:assert len(str(path))>260
    filesystem_path(path).write_bytes(blob+b'changed')
    with pytest.raises(ValueError, match='文件已变化'):bundle_file(store, bundle['id'])
    filesystem_path(report).write_bytes(b'changed Word')
    with pytest.raises(ValueError, match='材料已变化'):release_file(store, release['id'])
    assert store.one('briefs', brief['id'])['hash']==brief['hash']


@pytest.mark.parametrize('location', ['release', 'packet', 'word', 'audit'])
def test_junction_cannot_redirect_formal_or_audit_writes(case, tmp_path, location):
    import _winapi
    store, source, brief, _=case
    if location=='audit':
        release, _=complete_release(store, brief)
        job=enqueue_bundle(store, release['id'], {source['id']:{'mode':'original'}})
        link=store.root/'exports'/job['id']
    else:
        queued=enqueue_release(store, brief['id']);job=queued['job']
        link=store.root/'releases'/queued['release']['id']
        if location=='packet':link=link/'packet'
        if location=='word':link=store.root/'exports'/job['id']
    filesystem_path(link.parent).mkdir(parents=True, exist_ok=True)
    target=tmp_path/'unrelated owned delivery';target.mkdir()
    marker=target/'keep.txt';marker.write_bytes(b'unchanged target')
    _winapi.CreateJunction(str(target), str(filesystem_path(link)))
    try:
        with pytest.raises(ValueError, match='链接|越界'):
            (generate_bundle if location=='audit' else generate_release)(store, job, threading.Event())
        assert {p.name for p in target.iterdir()}=={'keep.txt'}
        assert marker.read_bytes()==b'unchanged target'
    finally:
        os.rmdir(filesystem_path(link))  # Remove the owned junction, never its target.


def test_safe_file_rejects_same_workspace_junction_and_root_alias(case):
    import _winapi
    store, _, _, _=case
    target=store.root/'owned-original';filesystem_path(target).mkdir()
    filesystem_path(target/'same.txt').write_bytes(b'original identity')
    link=store.root/'owned-alias'
    _winapi.CreateJunction(str(target), str(filesystem_path(link)))
    try:
        assert safe_file(store.root, 'owned-original/same.txt')==target/'same.txt'
        with pytest.raises(ValueError, match='链接'):safe_file(store.root, 'owned-alias/same.txt')
        with pytest.raises(ValueError, match='链接'):safe_file(link, 'same.txt')
        assert filesystem_path(target/'same.txt').read_bytes()==b'original identity'
    finally:
        os.rmdir(filesystem_path(link))
