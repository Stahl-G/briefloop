import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('periodic_preflight', Path(__file__).resolve().parents[1] / 'experiments/periodic_reports/preflight.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def manifest(tmp_path):
    settings = dict(code_commit='a' * 40, prompt_sha256='1' * 64, skills_sha256='2' * 64,
                    model='test-only', effort='medium', tool_conditions='offline', rules_sha256='3' * 64, attempts=1)
    cases = []
    for n, split in enumerate(('development', 'same_series_future', 'cross_series'), 1):
        artifacts = []
        for role in ('input', 'reference_final'):
            path = f'{n}-{role}.txt'; content = path.encode(); (tmp_path / path).write_bytes(content)
            artifacts.append(dict(path=path, role=role, sha256=hashlib.sha256(content).hexdigest(),
                disclosure_family=path, available_at=f'2026-0{n}-01T00:00:00Z', availability_evidence='synthetic timestamp fixture'))
        cases.append(dict(id=f'case-{n}', series_id='A' if n < 3 else 'B', period=f'2026-0{n}',
            period_end=f'2026-0{n}-27T00:00:00Z', cutoff=f'2026-0{n}-28T00:00:00Z', requirements='check totals',
            missing_data='none', status='eligible', split=split, origin='synthetic', artifacts=artifacts))
    return dict(protocol=module.PROTOCOL, missing_data_policy='exclude_and_capture_prospectively',
                comparison=dict(baseline=settings, candidate=copy.deepcopy(settings)), cases=cases)


def reasons(result):
    return ' '.join(e['reason'] for e in result['errors'])


def test_valid_is_read_only_and_not_quality_claim(tmp_path):
    data = manifest(tmp_path); frozen = copy.deepcopy(data)
    result = module.validate(data, tmp_path)
    assert result['valid'] and result['synthetic_cases'] == 3
    assert 'no quality gain' in result['claim']
    assert data == frozen


@pytest.mark.parametrize('kind', ['future', 'unknown', 'no_evidence', 'hash', 'path'])
def test_inputs_fail_closed(tmp_path, kind):
    data = manifest(tmp_path); a = data['cases'][1]['artifacts'][0]
    if kind == 'future': a['available_at'] = '2026-03-01T00:00:00Z'
    if kind == 'unknown': a['available_at'] = None
    if kind == 'no_evidence': a['availability_evidence'] = ''
    if kind == 'hash': a['sha256'] = '0' * 64
    if kind == 'path': a['path'] = '../outside'
    assert not module.validate(data, tmp_path)['valid']


@pytest.mark.parametrize('kind', ['family', 'hash', 'final_as_input', 'series', 'chronology'])
def test_holdout_leakage(tmp_path, kind):
    data = manifest(tmp_path); dev, future, cross = data['cases']
    if kind == 'family': future['artifacts'][0]['disclosure_family'] = dev['artifacts'][0]['disclosure_family']
    if kind == 'hash': future['artifacts'][0] = copy.deepcopy(dev['artifacts'][0])
    if kind == 'final_as_input':
        a = copy.deepcopy(future['artifacts'][1]); a['role'] = 'input'; future['artifacts'][0] = a
    if kind == 'series': cross['series_id'] = 'A'
    if kind == 'chronology': future['period_end'] = dev['period_end']
    assert not module.validate(data, tmp_path)['valid']


def test_missing_history_excluded_not_reconstructed(tmp_path):
    data = manifest(tmp_path)
    missing = copy.deepcopy(data['cases'][2]); missing.update(id='excluded', status='not_backtestable', missing_data='originals unavailable', artifacts=[])
    data['cases'].append(missing)
    result = module.validate(data, tmp_path)
    assert result['valid'] and result['eligible_cases'] == 3
    assert result['excluded'] == [{'id': 'excluded', 'reason': 'originals unavailable'}]
    for c in data['cases']: c['status'] = 'not_backtestable'
    assert not module.validate(data, tmp_path)['valid']


@pytest.mark.parametrize('field,value', [('series_id', []), ('split', []), ('id', {}), ('cutoff', '2026-01-01'), ('artifacts', None)])
def test_bad_shape_reports_invalid(tmp_path, field, value):
    data = manifest(tmp_path); data['cases'][0][field] = value
    assert not module.validate(data, tmp_path)['valid']


def test_freeze_attempts_and_protocol_required(tmp_path):
    data = manifest(tmp_path); data['comparison']['candidate']['attempts'] = True
    data['protocol'] = 'old-semantic-v1'
    assert not module.validate(data, tmp_path)['valid']


def test_mutable_commit_rejected(tmp_path):
    data = manifest(tmp_path); data['comparison']['baseline']['code_commit'] = 'main'
    assert not module.validate(data, tmp_path)['valid']


def test_symlink_loop_is_structured_error(tmp_path):
    data = manifest(tmp_path)
    (tmp_path / 'loop').symlink_to('loop')
    data['cases'][0]['artifacts'][0]['path'] = 'loop'
    result = module.validate(data, tmp_path)
    assert not result['valid'] and 'artifact must exist' in reasons(result)
