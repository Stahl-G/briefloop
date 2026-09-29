"""Native packet IO and recovery guards; no model or semantic score is simulated."""
import json
import os
from types import SimpleNamespace

import pytest

from briefloop.interactive_runtime import InteractiveRuntime, _write
from briefloop.platform_support import filesystem_path
from briefloop.review import _archive_review_output, _packet, _review_requirement_index, build_packet, sha
from briefloop.runtime import Worker
from briefloop.sources import upload
from briefloop.store import Store, dump


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native paths and directory junctions')


@pytest.fixture(params=[False, True], ids=['short', 'long-unicode'])
def case(tmp_path, request):
    name='中文审阅工作区-'
    if request.param:name+='w'*(224-len(str(tmp_path))-1-len(name))
    store=Store(tmp_path/name)
    source=upload(store, '合成原件.txt', 'Revenue was 12 million USD. 🧭'.encode('utf-8'))
    run=store.create_run({'title':'合成报告', 'objective':'Explain revenue'}, [source['id']])
    brief=store.publish(run['id'], {'title':'合成报告', 'markdown':'Revenue was 12 million USD.'}, author='example')
    job=store.enqueue('review', {'version_id':brief['id'],
        'runtime':{'model':'gpt-6-luna', 'reasoning_effort':'high'}})
    return store, brief, job


def packet_case(case):
    store, brief, job=case
    folder=Worker(store).folder(job)
    fingerprint, files=build_packet(store, brief['id'], folder)
    review={'version_id':brief['id'], 'fingerprint':fingerprint,
        'data':{'packet_path':str((folder/'packet').relative_to(store.root)), 'files':files}}
    return store, brief, job, folder, review


def test_packet_originals_hashes_display_index_and_rejected_bytes_survive(case):
    store, brief, job, folder, review=packet_case(case)
    packet, target, files=_packet(store, review)
    assert packet==folder/'packet' and target['version_id']==brief['id']
    assert not str(packet).startswith('\\\\?\\') and all('\\' not in name for name in files)
    original=next(row for row in target['sources'] if row.get('original_hash'))
    index=json.loads(filesystem_path(packet/'index.json').read_text(encoding='utf-8'))
    source=next(row for row in index['sources'] if row['id']==original['id'])
    assert sha(filesystem_path(packet/source['original_file']).read_bytes())==original['original_hash']
    display=_review_requirement_index(store, {**review, 'data':dump(review['data'])})
    assert display['requirement_items'] and 'requirement_index_error' not in display
    rejected=b'{"status":"incomplete","invalid_utf8":"\xa1\xa1"}'
    filesystem_path(folder/'review.json').write_bytes(rejected)
    archived=_archive_review_output(folder)
    assert filesystem_path(folder/archived).read_bytes()==rejected
    assert _archive_review_output(folder)==archived
    filesystem_path(packet/'history/executions.json').write_bytes(b'[] changed')
    with pytest.raises(ValueError, match='核查包文件已变化'):_packet(store, review)
    assert store.one('briefs', brief['id'])['hash']==brief['hash']
    if len(str(store.root))==224:assert len(str(folder/'assessment.schema.json'))>260


def test_cached_transport_recovery_checks_same_model_and_reports_saved_progress(case):
    store, brief, job, folder, review=packet_case(case)
    configured=json.loads(job['payload'])['runtime']
    # This cached transport fixture explicitly has no admitted semantic review.
    _write(folder/'execution.json', {'runtime':configured, 'returncode':0, 'fixture':'transport only'})
    _write(folder/'review.json', {'status':'incomplete'})
    filesystem_path(folder/'events.jsonl').write_text(json.dumps({'type':'item.completed',
        'item':{'type':'agent_message', 'text':'合成传输记录，不代表模型审阅'}})+'\n', encoding='utf-8')
    stage={**job, 'readonly_output':'review.json'}
    runtime=InteractiveRuntime(store, harness=SimpleNamespace())
    assert runtime.execute(stage, 'Do not invoke a model', folder)['fixture']=='transport only'
    progress=json.loads(store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC", (job['id'],))[0]['data'])
    assert progress['message']=='合成传输记录，不代表模型审阅' and progress['draft_ready']
    _write(folder/'execution.json', {'runtime':{**configured, 'model':'different'}, 'returncode':0})
    with pytest.raises(ValueError, match='模型配置'):runtime.execute(stage, 'Do not invoke a model', folder)
    assert not store.rows('SELECT id FROM reviews')


@pytest.mark.parametrize('location', ['job', 'packet', 'nested'])
def test_directory_junction_cannot_redirect_packet_or_schema_writes(case, tmp_path, location):
    import _winapi
    store, brief, job=case
    folder=store.root/'jobs'/job['id']
    link=folder if location=='job' else folder/'packet'
    if location=='nested':link=link/'sources'
    filesystem_path(link.parent).mkdir(parents=True, exist_ok=True)
    target=tmp_path/'unrelated owned directory';target.mkdir()
    marker=target/'keep.txt';marker.write_bytes(b'preserve unrelated bytes')
    _winapi.CreateJunction(str(target), str(filesystem_path(link)))
    try:
        with pytest.raises(ValueError, match='链接|越界'):
            if location=='job':Worker(store).folder(job)
            else:build_packet(store, brief['id'], folder)
        assert {p.name for p in target.iterdir()}=={'keep.txt'}
        assert marker.read_bytes()==b'preserve unrelated bytes'
    finally:
        os.rmdir(filesystem_path(link))  # Remove the owned junction, never its target.


def test_unindexed_directory_junction_invalidates_an_existing_packet(case, tmp_path):
    import _winapi
    store, brief, job, folder, review=packet_case(case)
    target=tmp_path/'unrelated packet directory';target.mkdir()
    link=folder/'packet/extra-directory'
    _winapi.CreateJunction(str(target), str(filesystem_path(link)))
    try:
        with pytest.raises(ValueError, match='链接目录'):_packet(store, review)
        assert not list(target.iterdir())
    finally:
        os.rmdir(filesystem_path(link))
