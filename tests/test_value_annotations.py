"""Offline guards for annotation identity, complete answers and cross-model agreement."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1] / 'native-engine'
sys.path.insert(0, str(ENGINE))
import annotation_items as annotations
import label_sentences as labels


def label_cache(root, directory, *, sha='a' * 64, reader='Reader', text='Demand is falling.', second=False):
    path = root / directory / 'slice.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'slice': 'slice', 'rules': 'r2', 'markdown_sha256': sha, 'reader': reader,
            'paragraphs': [{'block': 1, 'sentences': [{'i': 0, 'text': text, 'label': 'fact'}]
                           + ([{'i': 1, 'text': 'Costs may rise.', 'label': 'implication'}] if second else [])}]}
    path.write_text(json.dumps(data))
    return data


def test_cache_identity_never_reuses_old_partial_or_changed_cache(tmp_path):
    entry = {'name': 'slice', 'version': 'v1'}
    requirements = {'title': 'T', 'objective': 'O', 'audience': 'A'}
    identity = labels.cache_identity(entry, 'Body', requirements, 'Reader', 'codex', 'model-a', 'prompt')
    path = tmp_path / 'cache.json'
    path.write_text(json.dumps({'markdown_sha256': identity['markdown_sha256']}))
    original = path.read_bytes()
    with pytest.raises(ValueError, match='original preserved'):
        labels.matching_cache(path, identity)
    assert path.read_bytes() == original
    path.unlink()
    labels.write_cache(path, {'complete': True, 'cache_identity': identity, 'paragraphs': []})
    assert labels.matching_cache(path, identity)
    for changes in ({'requested_model': 'model-b'}, {'rules': 'r3'}, {'prompt_sha256': 'changed'},
                    {'reader_sha256': 'changed'}):
        with pytest.raises(ValueError):
            labels.matching_cache(path, {**identity, **changes})
    cached = json.loads(path.read_text())
    cached['paragraphs'] = [{'error': 'altered'}]
    path.write_text(json.dumps(cached))
    with pytest.raises(ValueError):
        labels.matching_cache(path, identity)


def test_score_rejects_short_or_illegal_answers_without_zip_truncation(tmp_path):
    label_cache(tmp_path, 'labels', second=True)
    item = {'id': 's', 'kind': 'sentence', 'slice': 'slice', 'set': 'dev', 'block': 1,
            'sentences': ['Demand is falling.', 'Costs may rise.']}
    for answer in ({'labels': ['fact']}, {'labels': ['fact', 'unknown']}, {'labels': ['fact', 'implication', 'fact']}):
        report = annotations.score(tmp_path, [item], {'s': answer})
        assert report['sentences'] == 0 and report['items']['invalid'] == 1
    report = annotations.score(tmp_path, [item], {'s': {'labels': ['fact', 'implication']}})
    assert report['sentences'] == 2 and report['agreement'] == 1


def test_v2_disputes_and_chapters_have_separate_validated_denominators(tmp_path):
    items = [{'id': 'd', 'kind': 'dispute', 'slice': 'slice', 'set': 'dev', 'block': 1, 'i': 1,
              'context': ['Fact.', 'Judgment.'], 'votes': {'model-a': 'fact', 'model-b': 'implication'}},
             {'id': 'c', 'kind': 'chapter', 'slice': 'slice'},
             {'id': 'p', 'kind': 'paraphrase', 'original': 'a', 'paraphrase': 'b'}]
    report = annotations.score(tmp_path, items, {'d': {'value': 'implication'}, 'c': {'value': 'should'},
                                               'p': {'value': 'invented'}, 'not-an-item': {'value': 'fact'}})
    assert report['sentences'] == 0
    assert report['disputes']['by_annotator']['model-a']['agreement'] == 0
    assert report['disputes']['by_annotator']['model-b']['agreement'] == 1
    assert report['chapters'] == {'slice': 'should'}
    assert report['items'] == {'total': 3, 'valid': 2, 'missing': 0, 'invalid': 1, 'excluded': 0, 'unknown_answers': 1}
    assert annotations.score(tmp_path, items, {})['items']['missing'] == 3


def test_agree_reports_missing_text_hash_and_legacy_boundaries(tmp_path):
    label_cache(tmp_path, 'a', second=True)
    label_cache(tmp_path, 'b')
    report = annotations.agree(tmp_path, ['a', 'b'])
    assert report['sentences'] == 1
    assert report['coverage']['excluded_by_reason'] == {'missing_annotator': 1}
    assert report['coverage']['missing_by_annotator']['b'] == 1
    assert report['coverage']['identity_status'] == 'legacy-unverified'
    assert report['coverage']['verified_included_sentences'] == 0
    label_cache(tmp_path, 'b', text='Other text.')
    assert annotations.agree(tmp_path, ['a', 'b'])['coverage']['excluded_by_reason']['sentence_text_mismatch'] == 1
    label_cache(tmp_path, 'b', sha='b' * 64)
    report = annotations.agree(tmp_path, ['a', 'b'])
    assert report['sentences'] == 0 and report['pairs']['a vs b']['kappa'] is None
    assert report['coverage']['excluded_by_reason']['source_identity_mismatch'] == 1
    assert annotations.agree(tmp_path, ['missing-a', 'missing-b'])['unanimous'] is None


def test_paraphrase_checks_reject_a_changed_pair_before_calling_model(tmp_path, monkeypatch):
    import paraphrase_check as checker
    root = tmp_path / 'slices'
    (root / 'paraphrases').mkdir(parents=True)
    (root / 'paraphrases' / 'slice.json').write_text(json.dumps({'slice': 'slice', 'pairs': {'old': 'new'}}))
    out = tmp_path / 'checks.json'
    out.write_text(json.dumps({'slice#0': {'original': 'old', 'paraphrase': 'previous', 'value': 'same'}}))
    original = out.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['paraphrase_check.py', '--slices', str(root), '--out', str(out)])
    monkeypatch.setattr(checker, 'run_devin', lambda *_: pytest.fail('No model call is allowed before incompatible cache rejection'))
    with pytest.raises(ValueError, match='choose a new --out file'):
        checker.main()
    assert out.read_bytes() == original
