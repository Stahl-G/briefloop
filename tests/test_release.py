import json
import threading
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from briefloop.store import Store, dump, now, uid
from briefloop.evidence import create_span, create_claim, bind_claim, blocks
from briefloop.document_model import brief_document
from briefloop.deliverable_spec import (reader_contract_schema, requirement_items, resolve,
                                        validate_reader_contract)
from briefloop.review import build_packet, accept_review
from briefloop.release import (SCHEMA, eligibility, decision, enqueue_release, generate_release,
                               get_release, release_file, sha, validate_release)
from briefloop.audit_bundle import enqueue_bundle, generate_bundle, verify_bundle, bundle_file


def reviewed_report(tmp_path, figure=False, visual_sources=False, review_free_text=False, clause_protocol=False):
    store = Store(tmp_path)
    with store.tx() as c:
        c.executescript(SCHEMA)
    source = store.add_source('Synthetic public filing', 'Revenue was USD 12 million.\nUnused private appendix line.')
    visual_sources_to_bind = []
    if visual_sources:
        from PIL import Image
        from pypdf import PdfWriter
        from briefloop.sources import upload
        png = BytesIO()
        Image.new('RGB', (80, 50), 'green').save(png, format='PNG')
        image_source = upload(store, 'Synthetic confidential appendix.png', png.getvalue())
        pdf = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=120, height=160)
        writer.write(pdf)
        pdf_source = upload(store, 'Synthetic confidential appendix.pdf', pdf.getvalue())
        visual_sources_to_bind = [(image_source, {'kind': 'image'}, 'The image appendix is available.'),
                                  (pdf_source, {'kind': 'pdf', 'page': 1}, 'The PDF appendix is available.')]
    requirements = {'title': 'Revenue report', 'objective': 'Explain revenue', 'manual_sections': ['Financing']}
    if clause_protocol:
        store.set_meta('settings', {**store.settings(), 'company_context_enabled': False})
        requirements = {'title': 'Internal report', 'objective': 'Explain revenue', 'writing_mode': 'internal_report'}
    run = store.create_run(requirements, [source['id'], *[item[0]['id'] for item in visual_sources_to_bind]])
    markdown = 'Revenue was USD 12 million.\n\nFinancing: pending.'
    markdown += ''.join('\n\n' + item[2] for item in visual_sources_to_bind)
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
        message = chat.message(session['id'], 'Synthetic figure calculation', status='completed', turn_id='synthetic')
        chat.event(session['id'], 'job/attached', {'jobId': job['id']})
        store.event(job['id'], 'runtime_started', {'folder': str(store.root), 'session_id': session['id'],
                    'message_id': message['id'], 'backend': 'synthetic', 'runtime': {'model': 'synthetic/fixture'}})
        journal_tool(chat, session['id'], 'synthetic', 'tool-1', 'bash',
                     {'command': 'python calculate.py', 'api_key': 'DO-NOT-INCLUDE-THIS'},
                     '12000000', status='completed', exit_code=0)
    extra = {}
    if clause_protocol:
        spec = resolve(json.loads(run['requirements']))
        contract = validate_reader_contract(spec, {
            'source_fingerprint': reader_contract_schema(spec)['properties']['source_fingerprint']['const'],
            'clauses': [{'requirement_id': spec['requirement_items'][0]['requirement_id'],
                         'kind': 'reader_content', 'source_quote': 'Explain revenue', 'instruction': 'Explain revenue'}]})
        compiled = resolve(json.loads(run['requirements']), reader_contract=contract)
        extra['reader_contract'] = contract
    brief = store.publish(run['id'], {'title': 'Revenue report', 'markdown': markdown, **extra})
    span = create_span(store, {'source_id': source['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    claim = create_claim(store, run['id'], {'statement': 'Revenue was USD 12 million.', 'kind': 'fact',
        'supports': [{'span_id': span['id'], 'supports_quote': 'Revenue was USD 12 million.'}]})
    block_id = next(iter(blocks(brief_document(brief))))
    bind_claim(store, brief['id'], claim['id'], block_id, 'Revenue was USD 12 million.')
    visual_claims = []
    for visual_source, locator, statement in visual_sources_to_bind:
        from briefloop.evidence import node_text
        visual_span = create_span(store, {'source_id': visual_source['id'], 'locator': locator})
        visual_claim = create_claim(store, run['id'], {'statement': statement, 'kind': 'fact',
            'supports': [{'span_id': visual_span['id'], 'supports_quote': statement}]})
        visual_block = next(identity for identity, node in blocks(brief_document(brief)).items() if node_text(node) == statement)
        bind_claim(store, brief['id'], visual_claim['id'], visual_block, statement)
        visual_claims.append(visual_claim)
    folder = store.root / 'review_fixture'
    fingerprint, files = build_packet(store, brief['id'], folder)
    review_id = uid('review')
    review_job = store.enqueue('review', {'version_id': brief['id']})
    with store.tx() as c:
        c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)', (review_id, brief['id'], review_job['id'], fingerprint, 'running',
                  dump({'packet_path': 'review_fixture/packet', 'files': files,
                        'protocol': 'clauses_v1' if clause_protocol else 'legacy'}), None, now(), now()))
    # Synthetic transport events exercise export identity; they are not real
    # model evidence and are deliberately labelled synthetic in the fixture.
    from briefloop.chat_store import ChatStore
    from briefloop.execution_records import journal_tool
    chat = ChatStore(store)
    session = chat.create('Synthetic Reviewer transport', {'model': 'synthetic/fixture'}, folder)
    message = chat.message(session['id'], 'Synthetic target review', status='completed', turn_id='synthetic-turn')
    store.event(review_job['id'], 'runtime_started', {'folder': str(folder), 'session_id': session['id'],
                'message_id': message['id'], 'backend': 'synthetic', 'runtime': {'model': 'synthetic/fixture'}})
    journal_tool(chat, session['id'], 'synthetic-turn', 'read-target', 'read',
                 {'filePath': str(folder / 'packet/target.json')}, 'Synthetic target read', status='completed')
    result = {'fingerprint': fingerprint, 'version_id': brief['id'], 'status': 'complete', 'summary': 'Synthetic source checked',
        'coverage_scan_complete': True,
        'claim_checks': [{'claim_id': claim['id'], 'status': 'supported_for_scope', 'reason': 'Exact saved source line'}],
        'requirement_checks': [{'requirement_id': item['requirement_id'], 'status': 'manual' if item['mode'] == 'manual' else 'covered',
                                'reason': 'Actual body checked'} for item in requirement_items(requirements)],
        'assessment': {'brief_hash': brief['hash'], 'status': 'complete', 'summary': 'Synthetic', 'overall': '达到要求',
                       'evidence': 4, 'coverage': 4, 'analysis': 4, 'expression': 4}}
    if clause_protocol:
        result.update(_clause_review(compiled, {'reader_content': 'covered'}))
        result['requirement_checks'] = []
    result['claim_checks'].extend({'claim_id': item['id'], 'status': 'supported_for_scope', 'reason': 'Synthetic saved visual source fixture'} for item in visual_claims)
    if review_free_text:
        private_excerpt=store.source_text(source['id']).splitlines()[1]
        assert private_excerpt not in brief['markdown'] and private_excerpt not in span['data']['excerpt']
        result['summary']='Review notes quote an unused appendix: '+private_excerpt
        result['claim_checks'][0]['reason']='The selected line is enough; unused appendix: '+private_excerpt
        result['assessment']['summary']='Accepted report; appendix note: '+private_excerpt
        result['assessment']['checks']=[{'check':'optional appendix','notes':{private_excerpt:'unused'}}]
        result['requirement_checks'][0]['reason']='Covered without the appendix: '+private_excerpt
        result['unchecked_items']=[{'description':'Noncore appendix omitted from analysis: '+private_excerpt,'importance':'supporting'}]
        result['findings']=[{'kind':'expression','severity':'minor','claim_ids':[claim['id']],
            'block_ids':[block_id],'report_quote':'Revenue was USD 12 million.',
            'description':'Optional appendix phrasing: '+private_excerpt,'evidence':private_excerpt,
            'suggested_action':'Keep this appendix out of the report: '+private_excerpt}]
    accept_review(store, review_id, result)
    if review_free_text:
        from briefloop.review import respond
        finding=store.rows('SELECT id FROM review_findings WHERE review_id=?',(review_id,))[0]
        response=respond(store,finding['id'],brief['id'],'disagree','The appendix was not used: '+private_excerpt)
        folder=store.root/'review_followup_fixture'
        fingerprint,files=build_packet(store,brief['id'],folder)
        review_id=uid('review');review_job=store.enqueue('review',{'version_id':brief['id']})
        with store.tx() as connection:
            connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                (review_id,brief['id'],review_job['id'],fingerprint,'running',
                 dump({'packet_path':'review_followup_fixture/packet','files':files}),None,now(),now()))
        session=chat.create('Synthetic Reviewer followup',{'model':'synthetic/fixture'},folder)
        message=chat.message(session['id'],'Synthetic followup review',status='completed',turn_id='synthetic-followup')
        store.event(review_job['id'],'runtime_started',{'folder':str(folder),'session_id':session['id'],
            'message_id':message['id'],'backend':'synthetic','runtime':{'model':'synthetic/fixture'}})
        journal_tool(chat,session['id'],'synthetic-followup','read-history','read',
            {'filePath':str(folder/'packet/history/responses.json')},private_excerpt,status='completed')
        result={**deepcopy(result),'fingerprint':fingerprint,
            'response_checks':[{'response_id':response['id'],'decision':'dismissed_with_evidence',
                                'reason':'The report does not include this appendix: '+private_excerpt}]}
        accept_review(store,review_id,result)
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
    assert store.one('jobs', retry['job']['id'])['status'] == 'complete'
    # Formal state and file-job success commit together. A late stop cannot
    # turn this already-issued report's job into a cancelled task.
    from briefloop.runtime import Worker
    Worker(store).stop_job(retry['job']['id'])
    assert store.one('jobs', retry['job']['id'])['status'] == 'complete'
    assert get_release(store, result['release_id'])['status'] == 'released'
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
    legacy=deepcopy(blobs)
    legacy_manifest=json.loads(legacy['manifest.json']);legacy_manifest['schema_version']=1
    legacy['manifest.json']=dump(legacy_manifest).encode()
    legacy_output=BytesIO()
    with ZipFile(legacy_output,'w',ZIP_DEFLATED) as archive:
        for name,blob in legacy.items():archive.writestr(name,blob)
    assert verify_bundle(legacy_output.getvalue())['valid']
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


def test_restricted_sources_do_not_escape_through_figure_inputs(tmp_path, monkeypatch):
    import briefloop.figures as figures
    original_register = figures.register_figure

    def register_with_source_copy(store, run_id, image, title, caption, source_ids, data_path, script_path):
        raw = store.source_text(source_ids[0])
        data_path.write_text(dump({'raw_source_extract': raw, 'plotted_revenue_millions': 12}))
        script_path.write_text('source_text = ' + repr(raw) + '\nprint(12 * 1000000)')
        return original_register(store, run_id, image, title, caption, source_ids, data_path, script_path)

    monkeypatch.setattr(figures, 'register_figure', register_with_source_copy)
    store, source, brief, review = reviewed_report(tmp_path, figure=True, visual_sources=True, review_free_text=True)
    release, job = complete_release(store, brief)
    report_bytes = release_file(store, release['id']).read_bytes()
    all_source_ids = [item['id'] for item in release['data']['snapshot']['sources']]
    packet_index = json.loads((store.root / release['data']['packet_path'] / 'index.json').read_text())
    private_excerpt=store.source_text(source['id']).splitlines()[1]
    assert private_excerpt in release['data']['review_result']['summary']
    assert private_excerpt in release['data']['review_result']['claim_checks'][0]['reason']
    assert any(private_excerpt in item['data']['evidence'] for item in release['data']['findings'])
    for history in ('reviews','responses'):
        assert private_excerpt in (store.root/release['data']['packet_path']/('history/'+history+'.json')).read_text()
    source_visuals = ['packet/' + name for item in packet_index['sources'] for name in item.get('visual_files', [])]
    assert len(source_visuals) == 2
    for mode in ('metadata', 'excerpt'):
        queued = enqueue_bundle(store, release['id'], {identity: mode for identity in all_source_ids})
        result = generate_bundle(store, queued, threading.Event())
        path = store.root / result['path']
        checked = verify_bundle(path)
        assert checked['valid'] and not checked['complete_materials'], checked
        with ZipFile(path) as archive:
            assert archive.read('report.docx') == report_bytes
            assert any(name.endswith('/image.png') for name in archive.namelist())
            assert not any(name.endswith(('/data.json', '/script.py')) for name in archive.namelist())
            assert not set(source_visuals).intersection(archive.namelist())
            visual_index = json.loads(archive.read('packet/visual-inputs.json'))
            assert {item.get('file') for item in visual_index['images']}.issuperset(name.removeprefix('packet/') for name in source_visuals)
            leaks=[name for name in archive.namelist() if private_excerpt.encode() in archive.read(name)]
            assert not leaks, {'mode':mode,'leaked_members':leaks}
            target=json.loads(archive.read('packet/target.json'))
            selected_span=next(item for binding in target['evidence']['bindings'] for item in binding['evidence'] if item['source_id']==source['id'])
            if mode=='excerpt':assert selected_span['data']['excerpt']=='Revenue was USD 12 million.'
            else:assert 'excerpt' not in selected_span['data']
            restricted_blobs={name:archive.read(name) for name in archive.namelist()}
        missing = [item for item in checked['omissions'] if item.get('kind') == 'source_derived_figure']
        assert len(missing) == 2 and all(source['id'] in item['source_ids'] for item in missing)
        assert all(any(item.get('file') == name and item.get('source_id') in all_source_ids for item in checked['omissions']) for name in source_visuals)
        if mode=='metadata':
            def packed(blobs):
                output=BytesIO()
                with ZipFile(output,'w',ZIP_DEFLATED) as archive:
                    for name,blob in blobs.items():archive.writestr(name,blob)
                return output.getvalue()
            tampered=deepcopy(restricted_blobs)
            records=json.loads(tampered['records.json'])
            records['review_result']['findings'][0]['evidence']=private_excerpt
            tampered['records.json']=dump(records).encode()
            manifest=json.loads(tampered['manifest.json'])
            manifest['files']['records.json']=sha(tampered['records.json'])
            for transformation in manifest['transformations']:
                if transformation['file']=='records.json':transformation['included_sha256']=sha(tampered['records.json'])
            tampered['manifest.json']=dump(manifest).encode()
            checked_tamper=verify_bundle(packed(tampered))
            assert not checked_tamper['valid'] and any('未投影的自由文本' in error for error in checked_tamper['errors'])
            legacy=deepcopy(restricted_blobs)
            manifest=json.loads(legacy['manifest.json']);manifest['schema_version']=1
            legacy['manifest.json']=dump(manifest).encode()
            legacy_blob=packed(legacy)
            assert not verify_bundle(legacy_blob)['valid']
            path.write_bytes(legacy_blob)
            store.update_job(queued['id'],'complete',result={**result,'sha256':sha(legacy_blob)})
            with pytest.raises(ValueError,match='旧受限审计包'):bundle_file(store,queued['id'])
            assert enqueue_bundle(store,release['id'],{identity:mode for identity in all_source_ids})['id']!=queued['id']
    # The user can still explicitly include originals and corresponding full
    # calculation inputs; restrictions do not delete the stored artifacts.
    queued = enqueue_bundle(store, release['id'], {identity: 'original' for identity in all_source_ids})
    result = generate_bundle(store, queued, threading.Event())
    with ZipFile(store.root / result['path']) as archive:
        assert set(source_visuals).issubset(archive.namelist())
        records=json.loads(archive.read('records.json'))
        assert private_excerpt in records['review_result']['summary']
        assert private_excerpt in records['review_result']['claim_checks'][0]['reason']
        assert private_excerpt in records['review_result']['findings'][0]['evidence']
        assert private_excerpt in records['review_result']['response_checks'][0]['reason']
        assert any(private_excerpt in item['data']['evidence'] for item in records['findings'])
        for history in ('reviews','responses'):
            assert private_excerpt.encode() in archive.read('packet/history/'+history+'.json')
        assert any(b'Unused private appendix line.' in archive.read(name)
                   for name in archive.namelist() if name.endswith(('/data.json', '/script.py')))
        blobs = {name: archive.read(name) for name in archive.namelist()}
    # Merely relabelling a full package as metadata does not make its embedded
    # source images authorized; offline validation checks that relationship.
    manifest = json.loads(blobs['manifest.json'])
    for permission in manifest['source_permissions'].values():
        permission['mode'] = 'metadata'
    manifest['complete_materials'] = False
    blobs['manifest.json'] = dump(manifest).encode()
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, blob in blobs.items():
            archive.writestr(name, blob)
    checked = verify_bundle(output.getvalue())
    assert not checked['valid'] and any('受限来源的原图或页面图' in error for error in checked['errors'])


def test_offline_checks_all_frozen_review_files_and_tool_index(tmp_path):
    store, source, brief, review = reviewed_report(tmp_path, figure=True)
    release, job = complete_release(store, brief)
    queued = enqueue_bundle(store, release['id'], {source['id']: 'original'})
    result = generate_bundle(store, queued, threading.Event())
    with ZipFile(store.root / result['path']) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}
    tool_file = next(name for name in original if name.startswith('packet/history/tools/'))
    view_file = next(name for name in original if name.endswith('.view.json'))
    for operation, name in [('delete', tool_file), ('change', view_file)]:
        blobs = dict(original)
        manifest = json.loads(blobs['manifest.json'])
        if operation == 'delete':
            del blobs[name]
            del manifest['files'][name]
        else:
            blobs[name] = blobs[name].replace(b'12 million', b'120 million')
            manifest['files'][name] = sha(blobs[name])
        blobs['manifest.json'] = dump(manifest).encode()
        output = BytesIO()
        with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
            for filename, blob in blobs.items():
                archive.writestr(filename, blob)
        checked = verify_bundle(output.getvalue())
        assert not checked['valid'] and not checked['complete_materials']
        assert any(name in error or name.removeprefix('packet/') in error for error in checked['errors'])


def _saved_contract(base, kind, quote):
    return validate_reader_contract(base, {
        'source_fingerprint': reader_contract_schema(base)['properties']['source_fingerprint']['const'],
        'clauses': [{'requirement_id': base['requirement_items'][0]['requirement_id'],
                     'kind': kind, 'source_quote': quote, 'instruction': quote}]})


def _review_with(covered_ids):
    return {'status': 'complete', 'coverage_scan_complete': True,
            'requirement_checks': [{'requirement_id': i, 'status': 'covered', 'reason': 'checked'} for i in covered_ids]}


def test_writing_gate_follows_meaning_not_input_field():
    sentence = '不要重复免责声明。'
    empty = {'evidence': {'bindings': []}, 'conflicts': []}
    # 1) The sentence lives in writing_preferences: a missing interpretation is a notice.
    pref = {'title': 'Internal report', 'objective': '说明客户交付变化。', 'writing_mode': 'internal_report',
            'writing_preferences': [sentence]}
    pref_spec = resolve(pref)
    by_kind = {item['kind']: item for item in pref_spec['requirement_items']}
    pref_result = decision({**empty, 'requirements': pref_spec},
                           _review_with([by_kind['objective']['requirement_id']]), [])
    assert pref_result['eligible'] and pref_result['notices'][0]['code'] == 'writing_preference'
    # 2) The same sentence inside the objective, classified as a writing preference, is
    #    still soft: the gate follows the meaning, not the field it was typed into.
    obj = {'title': 'Internal report', 'objective': sentence, 'writing_mode': 'internal_report'}
    obj_soft = resolve(obj, reader_contract=_saved_contract(resolve(obj), 'writing_preference', sentence))
    obj_result = decision({**empty, 'requirements': obj_soft}, _review_with([]), [])
    assert obj_result['eligible'] and obj_result['notices'][0]['code'] == 'writing_preference'
    # 3) A mixed objective that also carries real content stays hard even with a writing clause.
    mixed = {'title': 'Internal report', 'objective': '说明客户交付变化。' + sentence,
             'writing_mode': 'internal_report'}
    mixed_base = resolve(mixed)
    identity = mixed_base['requirement_items'][0]['requirement_id']
    contract = validate_reader_contract(mixed_base, {
        'source_fingerprint': reader_contract_schema(mixed_base)['properties']['source_fingerprint']['const'],
        'clauses': [{'requirement_id': identity, 'kind': 'reader_content', 'source_quote': '说明客户交付变化。', 'instruction': 'x'},
                    {'requirement_id': identity, 'kind': 'writing_preference', 'source_quote': sentence, 'instruction': 'y'}]})
    hard = resolve(mixed, reader_contract=contract)
    assert not decision({**empty, 'requirements': hard}, _review_with([]), [])['eligible']


def test_review_packet_refuses_to_drop_a_saved_contract(tmp_path, monkeypatch):
    from briefloop import review
    store = Store(tmp_path)
    source = store.add_source('Source', 'Evidence')
    store.set_meta('settings', {**store.settings(), 'company_context_enabled': False})
    req = {'title': 'Internal report', 'objective': '说明交付变化。不要重复免责声明。', 'writing_mode': 'internal_report'}
    run = store.create_run(req, [source['id']])
    base = resolve(json.loads(store.one('runs', run['id'])['requirements']))
    contract = _saved_contract(base, 'writing_preference', '不要重复免责声明。')
    brief = store.publish(run['id'], {'title': 'R', 'markdown': '正文', 'reader_contract': contract})
    original = review._snapshot(store, brief['id'])
    assert original['requirements']['reader_contract'] == original['detail']['reader_contract']
    dropped = {**original, 'requirements': {**original['requirements'], 'reader_contract': None}}
    monkeypatch.setattr(review, '_snapshot', lambda store, version_id: dropped)
    with pytest.raises(ValueError, match='读者约定'):
        build_packet(store, brief['id'], store.root / 'packet-drop')


def test_must_fix_expression_anchor_and_overall_consistency():
    from briefloop.models import must_fix, overall_inconsistent
    assert must_fix({'status': 'complete', 'overall': '建议修改', 'expression': 2})
    assert overall_inconsistent({'status': 'complete', 'overall': '达到要求', 'expression': 2})
    assert not must_fix({'status': 'complete', 'overall': '达到要求', 'expression': 3})
    assert not must_fix({'status': 'incomplete', 'overall': '评估未完成', 'expression': 2})
    assert not overall_inconsistent({'status': 'complete', 'overall': '建议修改', 'expression': 2})


def test_formal_gate_surfaces_open_and_unresolved_gaps():
    review = {'status': 'complete', 'coverage_scan_complete': True, 'requirement_checks': []}
    base = {'requirements': {'requirement_items': []}, 'evidence': {'bindings': []}, 'conflicts': []}
    open_gap = decision({**base, 'detail': {'gap_records': [
        {'related': '利润', 'impact': '利润改善无法由材料支持', 'status': 'open'}]}}, review, [])
    assert open_gap['eligible'] and open_gap['notices'][0]['code'] == 'delivery_gap_open'
    # Unresolved gaps are surfaced but do not block on their own; a core evidence gap
    # still blocks through the claim and conflict checks.
    unresolved = decision({**base, 'detail': {'gap_records': [
        {'related': '利润', 'impact': '单位成本缺口仍未解决', 'status': 'unresolved'}]}}, review, [])
    assert unresolved['eligible'] and unresolved['notices'][0]['code'] == 'delivery_gap_unresolved'


def _clause_contract(objective, clauses):
    spec = resolve({'title': 'Internal report', 'objective': objective, 'writing_mode': 'internal_report'})
    identity = spec['requirement_items'][0]['requirement_id']
    value = {'source_fingerprint': reader_contract_schema(spec)['properties']['source_fingerprint']['const'],
             'clauses': [{'requirement_id': identity, **clause} for clause in clauses]}
    return resolve({'title': 'Internal report', 'objective': objective, 'writing_mode': 'internal_report'},
                   reader_contract=validate_reader_contract(spec, value))


def _clause_review(spec, statuses):
    from briefloop.deliverable_spec import clause_items
    checks = [{'clause_id': clause['clause_id'], 'status': statuses[clause['kind']], 'reason': 'checked'}
              for clause in clause_items(spec)]
    return {'status': 'complete', 'coverage_scan_complete': True, 'clause_checks': checks}


def test_clause_protocol_keeps_writing_soft_and_content_hard():
    spec = _clause_contract('说明交付变化。不要重复免责声明。', [
        {'kind': 'reader_content', 'source_quote': '说明交付变化。', 'instruction': '说明交付变化'},
        {'kind': 'writing_preference', 'source_quote': '不要重复免责声明。', 'instruction': '不重复免责'}])
    base = {'evidence': {'bindings': []}, 'conflicts': []}
    # Content answered, writing unmet: the parent objective is not consulted, so this
    # stays eligible with a soft notice.
    soft = _clause_review(spec, {'reader_content': 'covered', 'writing_preference': 'missing'})
    result = decision({**base, 'requirements': spec}, soft, [], protocol='clauses_v1')
    assert result['eligible'] and any(n['code'] == 'clause_unmet' for n in result['notices'])
    # Content missing blocks even though the writing clause is fine.
    hard = _clause_review(spec, {'reader_content': 'partial', 'writing_preference': 'covered'})
    blocked = decision({**base, 'requirements': spec}, hard, [], protocol='clauses_v1')
    assert not blocked['eligible'] and blocked['blockers'][0]['code'] == 'content_unmet'
    # "Cannot confirm" never blocks; it is a notice.
    unverified = _clause_review(spec, {'reader_content': 'unverified', 'writing_preference': 'covered'})
    assert decision({**base, 'requirements': spec}, unverified, [], protocol='clauses_v1')['eligible']


def test_soft_requirement_finding_does_not_reblock_delivery():
    spec = _clause_contract('说明交付变化。不要重复免责声明。', [
        {'kind': 'reader_content', 'source_quote': '说明交付变化。', 'instruction': '说明交付变化'},
        {'kind': 'writing_preference', 'source_quote': '不要重复免责声明。', 'instruction': '不重复免责'}])
    objective = spec['requirement_items'][0]['requirement_id']
    review = _clause_review(spec, {'reader_content': 'covered', 'writing_preference': 'covered'})
    base = {'requirements': spec, 'evidence': {'bindings': []}, 'conflicts': []}
    soft_finding = [{'id': 'f', 'status': 'open', 'data': {
        'kind': 'expression', 'severity': 'major', 'description': '重复免责声明',
        'requirement_ids': [objective]}}]
    result = decision(base, review, soft_finding, protocol='clauses_v1')
    assert result['eligible'] and result['notices'][0]['code'] == 'finding_notice'


def test_factual_finding_under_soft_requirement_still_blocks():
    # A method clause is soft, but a factual or evidence finding it reveals is not.
    spec = _clause_contract('核对计划与实际状态。', [
        {'kind': 'research_method', 'source_quote': '核对计划与实际状态。', 'instruction': '核对计划与实际'}])
    objective = spec['requirement_items'][0]['requirement_id']
    review = _clause_review(spec, {'research_method': 'covered'})
    base = {'requirements': spec, 'evidence': {'bindings': []}, 'conflicts': []}
    for kind in ('contradiction', 'missing_binding', 'insufficient_evidence'):
        finding = [{'id': 'f', 'status': 'open', 'data': {
            'kind': kind, 'severity': 'major', 'description': '把计划写成已投产',
            'requirement_ids': [objective]}}]
        blocked = decision(base, review, finding, protocol='clauses_v1')
        assert not blocked['eligible'] and blocked['blockers'][0]['code'] == 'finding_unresolved', kind
    # Only a compliance-type finding on a purely soft requirement is a notice.
    soft = [{'id': 'f', 'status': 'open', 'data': {
        'kind': 'missing_requirement', 'severity': 'major', 'description': '方法说明缺失',
        'requirement_ids': [objective]}}]
    assert decision(base, review, soft, protocol='clauses_v1')['eligible']


def test_same_result_follows_the_persisted_protocol():
    # The stored protocol, not the presence of clause_checks, decides the gate.
    spec = _clause_contract('说明交付变化。', [
        {'kind': 'reader_content', 'source_quote': '说明交付变化。', 'instruction': '说明交付变化'}])
    review = _clause_review(spec, {'reader_content': 'covered'})
    base = {'requirements': spec, 'evidence': {'bindings': []}, 'conflicts': []}
    assert decision(base, review, [], protocol='clauses_v1')['eligible']
    legacy = decision(base, review, [], protocol='legacy')
    assert not legacy['eligible'] and legacy['blockers'][0]['code'] == 'requirement_unfinished'


def test_clause_review_releases_verify_in_full_and_restricted_audit_bundles(tmp_path):
    store, source, brief, review_id = reviewed_report(tmp_path, clause_protocol=True)
    release, _ = complete_release(store, brief)
    for permissions in ({source['id']: 'original'}, {source['id']: 'excerpt'}, {}):
        job = enqueue_bundle(store, release['id'], permissions)
        result = generate_bundle(store, job, threading.Event())
        checked = verify_bundle(store.root / result['path'])
        assert checked['valid'], checked
        with ZipFile(store.root / result['path']) as archive:
            records = json.loads(archive.read('records.json'))
            assert records['review_protocol'] == 'clauses_v1'
            if not permissions:
                assert records['snapshot']['requirements']['reader_contract'] == '[omitted: source export permissions]'
                assert records['review_clauses'][0]['instruction'] == '[omitted: source export permissions]'
