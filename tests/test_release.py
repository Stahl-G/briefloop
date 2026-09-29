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
    monkeypatch.setattr(review, '_snapshot', lambda store, version_id, snapshot_version=7: dropped)
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


def test_clause_finding_uses_own_kind_and_preserves_frozen_identity():
    from briefloop.deliverable_spec import clause_items
    spec = _clause_contract('说明交付变化。核对计划与实际。', [
        {'kind': 'reader_content', 'source_quote': '说明交付变化。', 'instruction': '说明变化'},
        {'kind': 'research_method', 'source_quote': '核对计划与实际。', 'instruction': '核对状态'}])
    clauses = clause_items(spec)
    content, method = clauses
    review = _clause_review(spec, {'reader_content': 'covered', 'research_method': 'covered'})
    base = {'requirements': spec, 'evidence': {'bindings': []}, 'conflicts': []}
    def check(identity, kind='execution_gap', frozen=None):
        finding = [{'id': 'f', 'status': 'open', 'data': {
            'kind': kind, 'severity': 'major', 'description': '未完成核对',
            'requirement_ids': [identity]}}]
        return decision(base, review, finding, protocol='clauses_v1', clauses=frozen)
    assert check(method['clause_id'])['eligible']
    assert not check(content['clause_id'])['eligible']
    assert not check(method['requirement_id'])['eligible']  # Mixed parent stays hard.
    assert not check(method['clause_id'], 'contradiction')['eligible']
    assert not check('clause_unknown')['eligible']
    redacted = [{**c, 'source_quote': '[omitted]', 'instruction': '[omitted]'} for c in clauses]
    assert check(method['clause_id'], frozen=redacted)['eligible']


def test_same_result_follows_the_persisted_protocol():
    # The stored protocol, not the presence of clause_checks, decides the gate.
    spec = _clause_contract('说明交付变化。', [
        {'kind': 'reader_content', 'source_quote': '说明交付变化。', 'instruction': '说明交付变化'}])
    review = _clause_review(spec, {'reader_content': 'covered'})
    base = {'requirements': spec, 'evidence': {'bindings': []}, 'conflicts': []}
    assert decision(base, review, [], protocol='clauses_v1')['eligible']
    legacy = decision(base, review, [], protocol='legacy')
    assert not legacy['eligible'] and legacy['blockers'][0]['code'] == 'requirement_unfinished'


def test_number_mismatch_and_broken_reference_block_release():
    snapshot = {'requirements': {'requirement_items': []}, 'evidence': {'bindings': []}, 'conflicts': [], 'detail': {},
                'deterministic': {'numbers': {'unmatched': [{'label': 'revenue', 'expected': '1.2 million USD', 'reason': '不一致'}],
                                               'skipped': [{'label': 'margin', 'reason': '缺少定位'}]},
                                  'broken_refs': ['source_missing']}}
    review = {'status': 'complete', 'coverage_scan_complete': True, 'claim_checks': [], 'requirement_checks': [],
              'conflict_checks': [], 'clause_checks': [], 'unchecked': [], 'unchecked_items': []}
    result = decision(snapshot, review, [])
    codes = {item['code'] for item in result['blockers']}
    assert {'number_mismatch', 'broken_reference'} <= codes
    assert any(item['code'] == 'number_unchecked' for item in result['notices'])
    assert result['eligible'] is False
