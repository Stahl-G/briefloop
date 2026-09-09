import json
import threading
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from briefloop.store import Store, dump, now, uid
from briefloop.evidence import create_span, create_claim, bind_claim, blocks
from briefloop.document_model import brief_document
from briefloop.deliverable_spec import requirement_items
from briefloop.review import build_packet, accept_review
from briefloop.release import (SCHEMA, eligibility, decision, enqueue_release, generate_release,
                               get_release, release_file, sha, validate_release)
from briefloop.audit_bundle import enqueue_bundle, generate_bundle, verify_bundle, bundle_file


def reviewed_report(tmp_path, figure=False):
    store = Store(tmp_path)
    with store.tx() as c:
        c.executescript(SCHEMA)
    source = store.add_source('Synthetic public filing', 'Revenue was USD 12 million.\nUnused private appendix line.')
    requirements = {'title': 'Revenue report', 'objective': 'Explain revenue', 'manual_sections': ['Financing']}
    run = store.create_run(requirements, [source['id']])
    markdown = 'Revenue was USD 12 million.\n\nFinancing: pending.'
    if figure:
        from PIL import Image
        from briefloop.figures import register_figure
        from briefloop.chat_store import ChatStore
        from briefloop.execution_records import journal_tool
        image = store.root / 'synthetic.png'
        Image.new('RGB', (30, 30), 'white').save(image)
        data = store.root / 'data.json'
        data.write_text('{"revenue_millions":12}')
        script = store.root / 'calculate.py'
        script.write_text('print(12 * 1000000)')
        saved = register_figure(store, run['id'], image, 'Revenue', 'USD millions', [source['id']], data, script)
        markdown += '\n\n' + saved['markdown']
        job = store.enqueue('generate', {'run_id': run['id']})
        chat = ChatStore(store)
        session = chat.create('Synthetic transport', {}, store.root)
        chat.event(session['id'], 'job/attached', {'jobId': job['id']})
        journal_tool(chat, session['id'], 'synthetic', 'tool-1', 'bash',
                     {'command': 'python calculate.py', 'api_key': 'DO-NOT-INCLUDE-THIS'},
                     '12000000', status='completed', exit_code=0)
    brief = store.publish(run['id'], {'title': 'Revenue report', 'markdown': markdown})
    span = create_span(store, {'source_id': source['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    claim = create_claim(store, run['id'], {'statement': 'Revenue was USD 12 million.', 'kind': 'fact',
        'supports': [{'span_id': span['id'], 'supports_quote': 'Revenue was USD 12 million.'}]})
    block_id = next(iter(blocks(brief_document(brief))))
    bind_claim(store, brief['id'], claim['id'], block_id, 'Revenue was USD 12 million.')
    folder = store.root / 'review_fixture'
    fingerprint, files = build_packet(store, brief['id'], folder)
    review_id = uid('review')
    review_job = store.enqueue('review', {'version_id': brief['id']})
    with store.tx() as c:
        c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)', (review_id, brief['id'], review_job['id'], fingerprint, 'running',
                  dump({'packet_path': 'review_fixture/packet', 'files': files}), None, now(), now()))
    # Synthetic transport events exercise export identity; they are not real
    # model evidence and are deliberately labelled synthetic in the fixture.
    from briefloop.chat_store import ChatStore
    from briefloop.execution_records import journal_tool
    chat = ChatStore(store)
    session = chat.create('Synthetic Reviewer transport', {'model': 'synthetic/fixture'}, folder)
    store.event(review_job['id'], 'runtime_started', {'folder': str(folder), 'session_id': session['id'],
                'backend': 'synthetic', 'runtime': {'model': 'synthetic/fixture'}})
    journal_tool(chat, session['id'], 'synthetic-turn', 'read-target', 'read',
                 {'filePath': str(folder / 'packet/target.json')}, 'Synthetic target read', status='completed')
    result = {'fingerprint': fingerprint, 'version_id': brief['id'], 'status': 'complete', 'summary': 'Synthetic source checked',
        'coverage_scan_complete': True,
        'claim_checks': [{'claim_id': claim['id'], 'status': 'supported_for_scope', 'reason': 'Exact saved source line'}],
        'requirement_checks': [{'requirement_id': item['requirement_id'], 'status': 'manual' if item['mode'] == 'manual' else 'covered',
                                'reason': 'Actual body checked'} for item in requirement_items(requirements)],
        'assessment': {'brief_hash': brief['hash'], 'status': 'complete', 'summary': 'Synthetic', 'overall': '达到要求',
                       'evidence': 4, 'coverage': 4, 'analysis': 4, 'expression': 4}}
    accept_review(store, review_id, result)
    return store, source, brief, review_id


def complete_release(store, brief):
    queued = enqueue_release(store, brief['id'])
    job = queued['job']
    result = generate_release(store, job, threading.Event())
    store.update_job(job['id'], 'complete', result=result)
    return get_release(store, queued['release']['id']), job


def test_formal_gate_separates_coverage_premises_requirements_and_minor_notices(tmp_path):
    store, source, brief, review = reviewed_report(tmp_path)
    checked = eligibility(store, brief['id'])
    assert checked['eligible'], checked
    snapshot = checked['input']['snapshot']
    result = checked['input']['review_result']
    missing = decision(snapshot, {**result, 'coverage_scan_complete': False}, [])
    assert not missing['eligible'] and missing['blockers'][0]['code'] == 'coverage_unchecked'
    assert not decision(snapshot, {**result, 'requirement_checks': []}, [])['eligible']
    assert not decision(snapshot, {**result, 'unchecked': ['Chart not viewable']}, [])['eligible']
    assert decision(snapshot, {**result, 'unchecked_items': [{'description': 'Optional background', 'importance': 'supporting'}]}, [])['eligible']
    assert not decision(snapshot, {**result, 'unchecked_items': [{'description': 'Core chart', 'importance': 'core'}]}, [])['eligible']
    findings = [{'id': 'minor', 'status': 'open', 'data': {'kind': 'expression', 'severity': 'major', 'description': 'Verbose'}}]
    assert decision(snapshot, result, findings)['eligible']
    findings[0]['data']['kind'] = 'contradiction'
    assert not decision(snapshot, result, findings)['eligible']
    premise = deepcopy(snapshot['evidence']['bindings'][0])
    inference = deepcopy(premise)
    inference['claim_id'] = 'inference'
    inference['evidence'] = []
    inference['premises'] = [premise]
    snapshot['evidence']['bindings'] = [inference]
    only_inference = {**result, 'claim_checks': [{'claim_id': 'inference', 'status': 'supported_for_scope', 'reason': 'Saved reasoning'}]}
    assert any(item.get('claim_id') == premise['claim_id'] for item in decision(snapshot, only_inference, [])['blockers'])


def test_release_keeps_fixed_file_history_and_rejects_races(tmp_path, monkeypatch):
    store, source, brief, review = reviewed_report(tmp_path)
    release, job = complete_release(store, brief)
    path = release_file(store, release['id'])
    from docx import Document
    assert 'Revenue was USD 12 million.' in '\n'.join(p.text for p in Document(path).paragraphs)
    again = enqueue_release(store, brief['id'])
    assert again['release']['id'] == release['id'] and again['job']['id'] == job['id']
    old_bytes = path.read_bytes()
    changed = store.revise(brief['id'], 'Revenue was USD 120 million.')
    assert not eligibility(store, changed['id'])['eligible']
    assert release_file(store, release['id']).read_bytes() == old_bytes
    # Even a successful renderer must not authorize changed evidence afterward.
    other, source2, brief2, review2 = reviewed_report(tmp_path / 'race')
    queued = enqueue_release(other, brief2['id'])
    import briefloop.export_jobs as exports
    original = exports.generate_word

    def mutate_after_render(*args):
        result = original(*args)
        (other.root / source2['path']).write_text('Revenue 120 million.')
        return result

    monkeypatch.setattr(exports, 'generate_word', mutate_after_render)
    with pytest.raises(ValueError, match='输入发生变化'):
        generate_release(other, queued['job'], threading.Event())
    assert get_release(other, queued['release']['id'])['status'] == 'pending'
    assert not eligibility(other, brief2['id'])['eligible']
    # The ordinary work-in-progress export remains independent of this gate.
    assert exports.enqueue_export(other, brief2['id'])['kind'] == 'export_docx'


def test_repeated_click_and_cancel_retry_preserve_formal_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store, source, brief, review = reviewed_report(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = list(pool.map(lambda unused: enqueue_release(store, brief['id']), range(2)))
    assert len({item['release']['id'] for item in requests}) == 1
    assert len({item['job']['id'] for item in requests}) == 1
    queued = requests[0]
    store.update_job(queued['job']['id'], 'cancelled')
    with pytest.raises(InterruptedError):
        generate_release(store, queued['job'], threading.Event())
    assert get_release(store, queued['release']['id'])['status'] == 'pending'
    retry = enqueue_release(store, brief['id'])
    assert retry['release']['id'] == queued['release']['id']
    assert retry['job']['id'] != queued['job']['id']
    result = generate_release(store, retry['job'], threading.Event())
    assert result['release_id'] == queued['release']['id']
    # Completion after a lost worker acknowledgement reuses the verified file.
    assert generate_release(store, retry['job'], threading.Event()) == result
    assert validate_release(store, result['release_id'])['version_id'] == brief['id']


def test_full_and_restricted_audit_packages_validate_offline_without_original_workspace(tmp_path):
    store, source, brief, review = reviewed_report(tmp_path)
    release, job = complete_release(store, brief)
    for permissions, complete in [({source['id']: 'original'}, True), ({source['id']: 'excerpt'}, False), ({}, False)]:
        queued = enqueue_bundle(store, release['id'], permissions)
        result = generate_bundle(store, queued, threading.Event())
        store.update_job(queued['id'], 'complete', result=result)
        path = bundle_file(store, queued['id'])
        copied = tmp_path / ('full.zip' if complete else ('excerpt.zip' if permissions else 'metadata.zip'))
        copied.write_bytes(path.read_bytes())
        checked = verify_bundle(copied)
        assert checked['valid'], checked
        assert checked['complete_materials'] is complete
        with ZipFile(copied) as archive:
            target = json.loads(archive.read('packet/target.json'))
            span = target['evidence']['bindings'][0]['evidence'][0]
            if not complete:
                assert not any(name.startswith('packet/sources/') for name in archive.namelist())
                assert 'located_text' not in span['data']
                assert checked['omissions']
            if not permissions:
                assert 'excerpt' not in span['data']
    # Verify ID/relationship corruption even when the attacker updates that
    # entry's hash in the manifest; this is not a cryptographic signature claim.
    original = tmp_path / 'full.zip'
    with ZipFile(original) as archive:
        blobs = {name: archive.read(name) for name in archive.namelist()}
    records = json.loads(blobs['records.json'])
    records['version_id'] = 'wrong_version'
    blobs['records.json'] = dump(records).encode()
    manifest = json.loads(blobs['manifest.json'])
    manifest['files']['records.json'] = sha(blobs['records.json'])
    blobs['manifest.json'] = dump(manifest).encode()
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        for name, blob in blobs.items():
            archive.writestr(name, blob)
    broken = verify_bundle(out.getvalue())
    assert not broken['valid'] and any('关联不一致' in item for item in broken['errors'])
    assert not verify_bundle(b'not a zip')['valid']


def test_audit_binds_figure_data_script_and_sanitized_tool_record(tmp_path):
    store, source, brief, review = reviewed_report(tmp_path, figure=True)
    release, job = complete_release(store, brief)
    queued = enqueue_bundle(store, release['id'], {source['id']: 'original'})
    result = generate_bundle(store, queued, threading.Event())
    with ZipFile(store.root / result['path']) as archive:
        names = archive.namelist()
        script = next(name for name in names if name.startswith('packet/figures/') and name.endswith('script.py'))
        assert archive.read(script) == b'print(12 * 1000000)'
        records = [archive.read(name) for name in names if name.startswith('packet/history/tools/')]
        assert len(records) == 1 and b'12000000' in records[0]
        assert b'DO-NOT-INCLUDE-THIS' not in records[0]
        blobs = {name: archive.read(name) for name in names}
    # Adjusting only archive checksums cannot hide a script/data mismatch with
    # the immutable figure's hashes and source association.
    blobs[script] = b'print(120 * 1000000)'
    manifest = json.loads(blobs['manifest.json'])
    manifest['files'][script] = sha(blobs[script])
    blobs['manifest.json'] = dump(manifest).encode()
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, blob in blobs.items():
            archive.writestr(name, blob)
    checked = verify_bundle(output.getvalue())
    assert not checked['valid'] and any('图表登记不一致' in text for text in checked['errors'])
