"""Planting/material integrity checks; no runtime, model or source network calls."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1] / 'native-engine'
sys.path.insert(0, str(NATIVE))
import experiment_value
import seed_value
import value_materials


def paragraph(subject, count=2):
    return ''.join(f'{subject}第{i + 1}项意味着收入和成本将受到这项政策变化的影响，企业需要根据已经明确的实施节点安排预算并核实订单。[@src_a]' for i in range(count))


def labels_for(markdown):
    blocks, body = seed_value._body_paragraphs(markdown)
    return {'paragraphs': [{'block': b, 'sentences': [{'i': i, 'text': seed_value._plain(s), 'label': 'implication'}
                                                   for i, s in enumerate(seed_value._sentences(blocks[b]))]}
                           for b in body]}


def test_removal_really_deletes_entire_paragraph_and_truth():
    markdown = '# 标题\n\n## 一节\n\n' + paragraph('A')
    for kind in ('conclusions_removed', 'implications_removed'):
        changed, truth = seed_value.degrade(markdown, kind, lambda _: '', labels=labels_for(markdown))
        assert changed == '# 标题\n\n## 一节'
        assert truth['removed'] == [s.strip() for s in seed_value._sentences(paragraph('A'))]
    bold = '# 标题\n\n## 一节\n\n**' + paragraph('A').replace('[@src_a]', '') + '**[@src_a]'
    changed, truth = seed_value.degrade(bold, 'implications_removed', lambda _: '', labels=labels_for(bold))
    assert changed == '# 标题\n\n## 一节' and len(truth['removed']) == 2
    blocks = [paragraph('A')]
    assert seed_value._remove(blocks, {(0, 99)}) == []
    assert blocks == [paragraph('A')]


def test_half_removal_counts_target_sentences_not_paragraphs():
    markdown = '# 标题\n\n## 一节\n\n' + paragraph('A', 3) + '\n\n' + paragraph('B', 2)
    changed, truth = seed_value.degrade(markdown, 'implications_half_removed', lambda _: '', labels=labels_for(markdown))
    assert truth['eligible_count'] == 5
    assert truth['selected_count'] == truth['removed_count'] == 2
    assert [item['i'] for item in truth['selected_targets']] == [0, 1]
    assert paragraph('B', 2) in changed
    assert 'A第3项' in changed and 'A第1项' not in changed
    one = '# 标题\n\n## 一节\n\n' + paragraph('A', 1) * 2
    # Only one marked implication among two sentence addresses.
    labels = labels_for(one)
    labels['paragraphs'][0]['sentences'][1]['label'] = 'fact'
    assert seed_value.degrade(one, 'implications_half_removed', lambda _: '', labels=labels) == (one, None)


def test_reorder_never_crosses_heading_and_unchanged_is_na():
    a, b, c, d = (paragraph(x) for x in 'ABCD')
    markdown = f'# 标题\n\n## 一节\n\n{a}\n\n{b}\n\n## 二节\n\n{c}\n\n{d}'
    changed, truth = seed_value.degrade(markdown, 'paragraphs_reordered', lambda _: '')
    assert changed == f'# 标题\n\n## 一节\n\n{b}\n\n{a}\n\n## 二节\n\n{d}\n\n{c}'
    assert len(truth['sections']) == 2
    spaced = markdown + '\n\n'
    reordered, _ = seed_value.degrade(spaced, 'paragraphs_reordered', lambda _: '')
    assert reordered == changed + '\n\n'
    single = f'# 标题\n\n## 一节\n\n{a}\n\n## 二节\n\n{b}'
    identical = f'# 标题\n\n## 一节\n\n{a}\n\n{a}'
    for original in (single, identical):
        assert seed_value.degrade(original, 'paragraphs_reordered', lambda _: '') == (original, None)
    identity_pairs = {seed_value._plain(s): seed_value._plain(s) for s in seed_value._sentences(a)}
    assert seed_value.degrade(single, 'implications_paraphrased', lambda _: '', identity_pairs, labels_for(single)) == (single, None)


@pytest.fixture
def frozen(tmp_path):
    slices = tmp_path / 'slices'
    workspace = slices / 'one'
    workspace.mkdir(parents=True)
    markdown = '# 标题\n\n## 一节\n\n' + paragraph('A')
    requirements = {'title': '需求', 'audience': '管理层', 'objective': '安排预算'}
    with sqlite3.connect(workspace / 'briefloop.db') as db:
        db.execute('CREATE TABLE briefs(id TEXT,markdown TEXT,run_id TEXT)')
        db.execute('CREATE TABLE runs(id TEXT,requirements TEXT)')
        db.execute('INSERT INTO briefs VALUES(?,?,?)', ('v1', markdown, 'r1'))
        db.execute('INSERT INTO runs VALUES(?,?)', ('r1', json.dumps(requirements, ensure_ascii=False)))
    selected = [{'name': 'one', 'version': 'v1', 'set': 'dev'}]
    (slices / 'manifest.json').write_text(json.dumps({'slices': selected}))
    materials = tmp_path / 'materials'
    materials.mkdir()
    identity = {'slice': 'one', 'version': 'v1', 'rules': 'r2', 'markdown_sha256': hashlib.sha256(markdown.encode()).hexdigest(),
                'reader_contract_sha256': value_materials.reader_contract_hash(requirements)}
    labels = {**identity, **labels_for(markdown)}
    original = labels['paragraphs'][0]['sentences'][0]['text']
    paraphrases = {**identity, 'pairs': {original: original.replace('收入和成本', '成本及收入')}, 'checks': {original: 'same'}}
    (materials / 'labels.json').write_text(json.dumps(labels, ensure_ascii=False))
    (materials / 'paraphrases.json').write_text(json.dumps(paraphrases, ensure_ascii=False))
    entry = {**{k: identity[k] for k in ('version', 'markdown_sha256', 'reader_contract_sha256')}, 'name': 'one',
             'labels_file': 'labels.json', 'paraphrases_file': 'paraphrases.json',
             'chapter': {'expectation': 'required', 'reason': '帮助管理层安排预算', 'scope': '本节政策影响'}}
    manifest = materials / 'manifest.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'rules_version': 'r2', 'slices': [entry]}, ensure_ascii=False))
    return slices, selected, manifest, labels, paraphrases


def test_verified_materials_load_without_mutating_source(frozen):
    slices, selected, manifest, _, _ = frozen
    original = (slices / 'one' / 'briefloop.db').read_bytes()
    material = value_materials.load_materials(manifest, slices, selected)['one']
    assert material['chapter']['expectation'] == 'required'
    assert len(material['paraphrases']) == 1
    # Disagreement remains unresolved and is never targeted as a fact or implication.
    labels = json.loads((manifest.parent / 'labels.json').read_text())
    labels['paragraphs'][0]['sentences'][1]['label'] = 'unresolved'
    (manifest.parent / 'labels.json').write_text(json.dumps(labels))
    material = value_materials.load_materials(manifest, slices, selected)['one']
    assert material['labels']['paragraphs'][0]['sentences'][1]['label'] == 'unresolved'
    assert len(seed_value.judgments(value_materials.read_frozen_version(slices / 'one', 'v1')[0], material['labels'])) == 1
    assert (slices / 'one' / 'briefloop.db').read_bytes() == original


@pytest.mark.parametrize('stale', ['markdown', 'reader'])
def test_stale_materials_fail_before_dispatch_or_network(frozen, tmp_path, monkeypatch, stale):
    slices, _, manifest, _, _ = frozen
    with sqlite3.connect(slices / 'one' / 'briefloop.db') as db:
        if stale == 'markdown':
            db.execute("UPDATE briefs SET markdown=markdown || '已改' WHERE id='v1'")
        else:
            db.execute("UPDATE runs SET requirements='{}' WHERE id='r1'")
    def forbidden(*args, **kwargs):
        raise AssertionError('No model dispatch or network is permitted')
    monkeypatch.setattr(experiment_value, 'ProcessPoolExecutor', forbidden)
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    out = tmp_path / 'out'
    monkeypatch.setattr(sys, 'argv', ['experiment_value.py', '--slices', str(slices), '--materials', str(manifest),
                                    '--kinds', 'control,implications_removed', '--out', str(out)])
    with pytest.raises(ValueError, match='Stale'):
        experiment_value.main()
    assert not out.exists()


@pytest.mark.parametrize('bad', ['sentence', 'uncertain', 'escape', 'missing'])
def test_incomplete_unchecked_or_wrong_address_materials_rejected(frozen, bad):
    slices, selected, manifest, labels, paraphrases = frozen
    if bad == 'sentence':
        labels['paragraphs'][0]['sentences'][0]['text'] += '不同'
        (manifest.parent / 'labels.json').write_text(json.dumps(labels))
    elif bad == 'uncertain':
        paraphrases['checks'] = {key: 'uncertain' for key in paraphrases['pairs']}
        (manifest.parent / 'paraphrases.json').write_text(json.dumps(paraphrases))
    elif bad == 'escape':
        data = json.loads(manifest.read_text())
        data['slices'][0]['labels_file'] = '../slices/manifest.json'
        manifest.write_text(json.dumps(data))
    else:
        (manifest.parent / 'labels.json').unlink()
    with pytest.raises(ValueError):
        value_materials.load_materials(manifest, slices, selected)


def test_failed_v2_leg_is_saved_without_legacy_cache_fallback(tmp_path):
    record = experiment_value.leg('one', 'v1', 'implications_removed', 0, 'unused', 'unused', tmp_path, tmp_path, None)
    assert record['status'] == 'failed'
    assert 'verified --materials' in record['error']
    assert json.loads((tmp_path / 'legs' / 'one-implications_removed-0.json').read_text()) == record


def test_summary_excludes_na_failure_and_unpaired_deltas():
    base = {'slice': 'one', 'repeat': 0, 'kind': 'implications_removed', 'route': 'evaluator',
            'scores': dict.fromkeys(experiment_value.DIMENSIONS, 4), 'overall': '建议修改', 'assessment_status': 'complete'}
    records = [{**base, 'status': 'complete'}, {**base, 'status': 'not_applicable'}, {**base, 'status': 'failed'}]
    summary = experiment_value.summarise(records)
    assert summary['scored_legs'] == 1 and summary['kinds'] == {}
    assert len(summary['not_applicable']) == len(summary['failed_legs']) == 1
    assert len(summary['scored_without_control']) == 1
    assert summary['rates']['implications_removed']['analysis_below_control_mean'] is None
    assert summary['rates']['implications_removed']['control_comparison_legs'] == 0


def test_explicit_slice_subset_stays_in_requested_split():
    manifest = {'slices': [{'name': 'a', 'set': 'dev'}, {'name': 'b', 'set': 'held'}]}
    assert experiment_value.select_slices(manifest, 'dev', 'a') == [manifest['slices'][0]]
    for names in ('', 'a,', 'a,a', 'b', 'missing'):
        with pytest.raises(ValueError):
            experiment_value.select_slices(manifest, 'dev', names)


def test_incomplete_assessment_and_missing_candidate_checks_are_reported():
    record = {'slice': 'one', 'kind': 'control', 'repeat': 0, 'status': 'complete', 'route': 'evaluator',
              'assessment_status': 'incomplete', 'scores': dict.fromkeys(experiment_value.DIMENSIONS, 4),
              'quality_candidate': True, 'assessment': {}}
    complete = {**record, 'repeat': 1, 'assessment_status': 'complete', 'assessment': {'analysis_checks': []}}
    summary = experiment_value.summarise([record, complete])
    assert summary['scored_legs'] == 1
    assert len(summary['unscored_complete']) == 1
    assert summary['candidate_checklist_coverage']['missing'] == 1
    assert summary['candidate_checklist_coverage']['empty'] == 1
    assert summary['candidate_checklist_coverage']['nonempty'] == 0
