"""Exercise supplied reader views and frozen closeout records without model calls."""
import json

import pytest

from briefloop import analyst, research_plan, review
from briefloop.runtime import assessment_prompt
from briefloop.scout_tools import join_scouts
from briefloop.store import Store, dump


def report(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Official release', 'Public beta is available. Revenue was USD 12 million.',
                              url='https://example.test/release')
    run = store.create_run({'title': 'Weekly', 'objective': 'Explain product status and revenue.'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Weekly', 'editor_document': {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Public beta is available. Revenue was USD 12 million.'},
        {'type': 'citation', 'attrs': {'sourceId': source['id']}}]}]},
        'citations': [{'source_id': source['id'], 'locator': 'line 1'}]})
    return store, run, source, brief


@pytest.mark.parametrize('backend', ['codex', 'briefloop-native'])
def test_evaluator_receives_actual_reader_view_but_missing_machine_records_stay_unchecked(tmp_path, backend):
    store, run, source, brief = report(tmp_path)
    folder = store.root / backend
    folder.mkdir()
    assessment_prompt(store, brief, folder, backend)
    packet = folder / 'packet' if backend == 'briefloop-native' else folder
    inputs = json.loads((packet / 'input.json').read_text(encoding='utf-8'))
    context = inputs['reading_context']
    preview = (packet / context['reader_preview_file']).read_text(encoding='utf-8')
    assert '[@' not in preview and '[1]' in preview
    assert 'Official release' in preview and 'https://example.test/release' in preview
    assert context['storage_citations']['source_ids'] == [source['id']]
    assert context['version_id'] == brief['id'] and context['brief_hash'] == brief['hash']
    assert context['machine_records']['number_binding_count'] == 0
    assert inputs['refcheck']['numbers']['status'] == 'not_checked'
    # Supplying an unbound but source-supported number never rewrites the report,
    # fabricates a semantic verdict, or silently creates binding/review records.
    assert inputs['brief']['markdown'] == brief['markdown']
    assert store.one('briefs', brief['id']) == brief
    assert store.rows('SELECT id FROM assessments') == []


def test_reviewer_reading_context_is_frozen_and_cannot_be_tampered_with(tmp_path):
    store, run, source, brief = report(tmp_path)
    fingerprint, files = review.build_packet(store, brief['id'], store.root / 'review')
    packet = store.root / 'review/packet'
    context_path = packet / 'reading-context.json'
    context = json.loads(context_path.read_text(encoding='utf-8'))
    assert context['machine_records']['number_binding_count'] == 0
    assert context['reader_preview_file'] in files
    assert 'reading-context.json' in files
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
            ('reading_review', brief['id'], None, fingerprint, 'running',
             dump({'packet_path': 'review/packet', 'files': files}), None, '2026', '2026'))
    review.validate_applicable_review(store, 'reading_review', brief['id'])
    context_path.write_text(context_path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='核查包文件已变化'):
        review.validate_applicable_review(store, 'reading_review', brief['id'])


def test_targeted_followup_closeout_reaches_writer_without_adding_rounds_or_closing_unchecked_gaps(tmp_path):
    store = Store(tmp_path)
    run = store.create_run({'title': 'Weekly', 'objective': 'Cover product changes and important safety disclosures.',
                            'allow_web': True}, [], research_protocol='quality_v1')
    rid = run['id']
    research_plan.freeze(store, rid, structure={'breadth': 2, 'depth': 2})
    source = store.add_source('Secondary article', 'A vendor announced an agent control product.')
    store.attach_source(rid, source['id'])
    first = store.root / 'first.json'
    first.write_text(dump({'sources': [], 'gaps': ['Important safety disclosure lacks original report'],
                           'retrieval_notes': [{'query': 'agent control', 'result': 'secondary article'}]}), encoding='utf-8')
    original = first.read_bytes()
    joined = join_scouts(store, [first], run_id=rid)
    gap_id = joined['gap_records'][0]['gap_id']
    first_dir = store.root / 'research' / rid / 'rounds/1'
    first_dir.mkdir(parents=True)
    (first_dir / 'handoff.json').write_text(dump({'learnings': [], 'covered': ['Vendor product'],
        'follow_ups': ['Fetch the original safety disclosure'], 'open_questions': ['Was intrusion successful?']}), encoding='utf-8')
    research_plan.finish_round(store, rid, summary='Retain vendor product; obtain original safety report before judging outcome.')
    assert research_plan.frozen(store, rid)['current_round_id'] is None
    # No automatic extra round: the main agent explicitly elects the bounded followup.
    opened = research_plan.begin_round(store, rid, target_gap_ids=[gap_id])
    primary = store.add_source('Original safety report', 'The event was disclosed this week. No evidence of a successful intrusion was observed.')
    store.attach_source(rid, primary['id'])
    second = store.root / 'second.json'
    second.write_text(dump({'sources': [{'source_id': primary['id'], 'locator': 'line 1',
        'excerpt': store.source_text(primary['id']), 'facts': ['Disclosed this week; no observed success.'],
        'coverage_status': 'read'}], 'gaps': []}), encoding='utf-8')
    second_dir = store.root / 'research' / rid / 'rounds/2'
    (second_dir / 'handoff.json').write_text(dump({'learnings': [], 'covered': ['Original disclosure'],
        'follow_ups': [], 'open_questions': ['Vendor primary announcement remains inaccessible']}), encoding='utf-8')
    research_plan.finish_round(store, rid, summary='Include the original safety disclosure; retain vendor attribution and original-source limitation.',
        gap_updates=[{'gap_id': gap_id, 'status': 'resolved', 'reason': 'Original report now retrieved.',
            'evidence': [{'source_id': primary['id'], 'locator': 'line 1', 'excerpt': store.source_text(primary['id'])}]}])
    closed = research_plan.frozen(store, rid)['rounds'][opened['round_id']]['outcome']
    # A late rewrite of the host file cannot rewrite the frozen closeout seen by writers.
    (second_dir / 'handoff.json').write_text(dump({'covered': ['Everything verified'], 'open_questions': []}), encoding='utf-8')
    final = join_scouts(store, [first, second], run_id=rid)
    writer = analyst.packet(store, rid, store.root / 'writer', plan={}, research=final)
    current = json.loads((writer['root'] / 'research.json').read_text(encoding='utf-8'))
    notes = [note for note in current['retrieval_notes'] if note.get('kind') == 'research_round_closeout']
    assert len(notes) == 2
    assert notes[-1]['summary'] == closed['summary']
    assert notes[-1]['open_questions'] == ['Vendor primary announcement remains inaccessible']
    assert any(item['gap_id'] == gap_id for item in current['gap_history'])
    assert len(research_plan.frozen(store, rid)['rounds']) == 2
    assert research_plan.frozen(store, rid)['current_round_id'] is None
    assert first.read_bytes() == original


def test_old_v8_length_snapshot_remains_applicable_without_rewriting_frozen_bytes(tmp_path, monkeypatch):
    store, run, source, brief = report(tmp_path)
    real_snapshot = review._snapshot

    def legacy_snapshot(store, version_id, snapshot_version=7):
        value = real_snapshot(store, version_id, snapshot_version)
        if 'length_stats' in value:
            for key in ('length_mode', 'length_requirement', 'strict_exceeded'):
                value['length_stats'].pop(key, None)
            value['length_stats']['limit_scope'] = 'Legacy upper range comparison scope.'
        return value

    with monkeypatch.context() as patch:
        patch.setattr(review, '_snapshot', legacy_snapshot)
        fingerprint, files = review.build_packet(store, brief['id'], store.root / 'legacy')
    packet = store.root / 'legacy/packet'
    before = {name: (packet / name).read_bytes() for name in files}
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
            ('legacy_v8', brief['id'], None, fingerprint, 'running',
             dump({'packet_path': 'legacy/packet', 'files': files}), None, '2026', '2026'))
    review.validate_applicable_review(store, 'legacy_v8', brief['id'])
    assert {name: (packet / name).read_bytes() for name in files} == before
    # New explicit strict requirements really change the input and cannot use
    # the compatibility projection to inherit an old review.
    requirements = json.loads(store.one('runs', run['id'])['requirements'])
    requirements.update(length_mode='strict', length_requirement={'kind': 'user_selection', 'text': 'At most 50 words.'},
                        target_words=30, max_words=50)
    with store.tx() as connection:
        connection.execute('UPDATE runs SET requirements=? WHERE id=?', (dump(requirements), run['id']))
    with pytest.raises(ValueError, match='依据发生变化'):
        review.validate_applicable_review(store, 'legacy_v8', brief['id'])


@pytest.mark.parametrize('backend', ['codex', 'briefloop-native'])
def test_evaluator_sees_current_resolution_separately_from_saved_report_gaps(tmp_path, backend):
    from briefloop.research_handoff import current_research
    store, run, source, original = report(tmp_path)
    missing = 'Original product release not yet obtained.'
    brief = store.publish(run['id'], {'title': 'Weekly', 'markdown': original['markdown'], 'gaps': [missing]})
    research_plan.freeze(store, run['id'])
    research = current_research(store, run['id'], {'sources': [], 'gaps': [missing]}, register=True)
    identity = research['gap_records'][0]['gap_id']
    research_plan.finish_round(store, run['id'], gap_updates=[{
        'gap_id': identity, 'status': 'resolved', 'reason': 'The original release is now available; it says public beta.',
        'evidence': [{'source_id': source['id'], 'locator': 'line 1', 'excerpt': 'Public beta is available.'}]}])
    before = store.one('briefs', brief['id'])
    folder = store.root / backend; folder.mkdir()
    assessment_prompt(store, brief, folder, backend)
    packet = folder / 'packet' if backend == 'briefloop-native' else folder
    inputs = json.loads((packet / 'input.json').read_text())
    context = inputs['research_context']
    assert context['version_id'] == brief['id'] and context['brief_hash'] == brief['hash']
    assert context['saved_report']['gaps'] == [missing]
    assert context['current_research']['gaps'] == []
    resolved = context['current_research']['gap_history'][0]
    assert resolved['gap_id'] == identity and resolved['evidence'][0]['source_hash'] == source['hash']
    assert 'public beta' in resolved['reason']
    assert store.one('briefs', brief['id']) == before


def test_current_research_context_requires_explicit_resolution_and_keeps_frozen_packets(tmp_path):
    from briefloop.research_handoff import current_research
    from briefloop.research_reading import snapshot
    store, run, source, brief = report(tmp_path)
    research_plan.freeze(store, run['id'])
    observed = current_research(store, run['id'], {'sources': [], 'gaps': ['Product status unverified']}, register=True)
    # An original source already exists: presence alone does not resolve a gap.
    before = snapshot(store, brief)
    assert before['current_research']['gaps'] == ['Product status unverified']
    research_plan.finish_round(store, run['id'], gap_updates=[{
        'gap_id': observed['gap_records'][0]['gap_id'], 'status': 'resolved', 'reason': 'Read the product announcement.',
        'evidence': [{'source_id': source['id'], 'locator': 'line 1', 'excerpt': 'Public beta is available.'}]}])
    resolved = snapshot(store, brief)
    assert resolved['current_research']['gaps'] == []
    assert resolved == snapshot(store, brief)  # Same recorded evidence, stable identity.
    fingerprint, files = review.build_packet(store, brief['id'], store.root / 'review-new')
    path = store.root / 'review-new/packet/research-context.json'
    frozen_bytes = path.read_bytes()
    assert json.loads(frozen_bytes) == resolved and 'research-context.json' in files
    # A replacement source snapshot invalidates the old proof, never the old packet bytes.
    with store.tx() as c:c.execute('UPDATE sources SET hash=? WHERE id=?', ('changed-hash', source['id']))
    current = snapshot(store, brief)
    assert current['current_research']['gap_records'][0]['status'] == 'open'
    assert current['current_research']['gap_records'][0]['validation_error']
    assert current['research_state_hash'] != resolved['research_state_hash']
    assert path.read_bytes() == frozen_bytes


def test_revision_writer_receives_saved_and_current_gap_context(tmp_path):
    from briefloop.research_reading import snapshot
    store, run, source, brief = report(tmp_path)
    context = snapshot(store, brief)
    packet = analyst.packet(store, run['id'], store.root / 'revision-context', plan={},
                            research={'sources': [], 'gaps': []}, base_version=brief['id'],
                            feedback={'research_context': context})
    value = json.loads((packet['root'] / 'input.json').read_text())
    assert value['research_context'] == context
    assert value['research_context']['brief_hash'] == brief['hash']
