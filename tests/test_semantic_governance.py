"""Synthetic fixtures exercise policy mechanics; no semantic quality evidence or network."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments' / 'jev_semantic'))
from assessment import construction_labels, evaluate, score_construction
from dataset import digest, freeze_dataset, load_dataset, make_case
from engine import execute, outcomes
from governance import evaluate_policy, freeze_policy, registry


def fixture_dataset(tmp_path):
    cases = []
    counts = {'dev': 0, 'validation': 0, 'test': 0}
    for i in range(1000):
        run = 'mechanical-fixture-' + str(i)
        bucket = int(digest(['mechanical', 'report:' + run])[:8], 16) % 10
        split = 'dev' if bucket < 6 else 'validation' if bucket < 8 else 'test'
        if counts[split] == 2:
            continue
        paragraph = '事实模板：10家。' if counts[split] == 0 else '附带模板：10号。'
        cases.append(make_case('numeric_omission', {'paragraph': paragraph,
            'target': {'text': '10', 'start': 5, 'end': 7}},
            {'run_id': run, 'version_id': run, 'sources': [], 'exposure': 'public'}, origin='synthetic'))
        counts[split] += 1
        if set(counts.values()) == {2}:
            break
    folder = tmp_path / 'dataset'
    freeze_dataset(cases, folder, seed='mechanical', metadata={'mechanical_fixture_only': True})
    manifest, cases = load_dataset(folder)
    labels = tmp_path / 'labels.jsonl'
    labels.write_text('\n'.join(json.dumps({'kind': 'label', 'source': 'construction',
        'case_id': c['case_id'], 'dataset_id': manifest['dataset_id'],
        'truth': 'fact' if c['state']['paragraph'].startswith('事实') else 'incidental'}) for c in cases) + '\n', encoding='utf-8')
    return folder, labels


def fake_transport(calls, probability=True, actual='fixture-model-v1'):
    def respond(endpoint, body, key, timeout):
        calls.append(body)
        # Known synthetic template outcomes test threshold plumbing, not model correctness.
        fact = json.loads(body['messages'][1]['content'])['paragraph'].startswith('事实')
        answer = {'choice': 'incidental'}
        if probability:
            answer['probabilities'] = {'fact': .4 if fact else .1, 'incidental': .5 if fact else .8, 'uncertain': .1}
        return {'model': actual, 'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
                'choices': [{'message': {'content': json.dumps(answer)}}]}
    return respond


def kwargs(calls, probability=True, **extra):
    return dict(provider='llm', model='fixture-model', endpoint='https://example.test/chat', key='fake-unit-key',
                allow_network=True, with_probabilities=probability, transport=fake_transport(calls, probability), **extra)


def policy_file(dataset, policy):
    return registry(dataset) / ('policy-' + policy['policy_id'] + '.json')


def test_dev_threshold_freeze_and_one_shot_heldouts(tmp_path):
    dataset, labels = fixture_dataset(tmp_path)
    calls = []
    dev = tmp_path / 'dev'
    execute(dataset, dev, **kwargs(calls))
    calibrated = freeze_policy(dataset, dev, labels, 'construction')
    policy = policy_file(dataset, calibrated)
    group = calibrated['groups']['numeric_omission:synthetic']
    assert .1 < group['threshold'] <= .4
    assert group['classification']['false_positive_count'] == group['classification']['false_negative_count'] == 0
    assert group['probability']['ece_10_equal_width_bins'] == pytest.approx(.35)
    assert calibrated['probability_alone_may_release'] is False
    assert calibrated['fit_split'] == 'dev'
    # Keep the cheaper discrete arm: it does not invent calibration measurements.
    choice_dev = tmp_path / 'choice-dev'
    execute(dataset, choice_dev, **kwargs([], False))
    baseline = freeze_policy(dataset, choice_dev, labels, 'construction')
    assert baseline['groups']['numeric_omission:synthetic']['threshold'] is None
    assert baseline['groups']['numeric_omission:synthetic']['probability']['ece_10_equal_width_bins'] is None
    assert baseline['run_identity']['response_shape'] == 'choice'
    with pytest.raises(ValueError, match='冻结策略'):
        execute(dataset, tmp_path / 'no-policy', **kwargs(calls, split='validation'))
    with pytest.raises(ValueError, match='封存'):
        execute(dataset, tmp_path / 'test-too-early', **kwargs(calls, split='test', frozen_policy=policy))
    with pytest.raises(ValueError, match='冻结策略'):
        execute(dataset, tmp_path / 'changed', **kwargs(calls, split='validation', timeout=31, frozen_policy=policy))
    validation = tmp_path / 'validation'
    execute(dataset, validation, **kwargs(calls, split='validation', frozen_policy=policy))
    assert len(calls) == 4
    execute(dataset, validation, **kwargs(calls, split='validation', frozen_policy=policy))
    assert len(calls) == 4  # Resume reuses the completed responses.
    with pytest.raises(ValueError, match='已被使用'):
        execute(dataset, tmp_path / 'second-validation', **kwargs(calls, split='validation', frozen_policy=policy))
    with pytest.raises(ValueError, match='不能再拟合'):
        freeze_policy(dataset, dev, labels, 'construction')
    with pytest.raises(ValueError, match='调参'):
        execute(dataset, tmp_path / 'new-dev', **kwargs(calls))
    with pytest.raises(ValueError, match='evaluate-policy'):
        score_construction(dataset, validation, labels, .01)
    with pytest.raises(ValueError, match='evaluate-policy'):
        evaluate(dataset, [validation], labels)
    assessment = evaluate_policy(dataset, validation, policy, labels)
    assert assessment['complete_for_frozen_evaluation'] is True
    assert assessment['execution']['usage_field_totals']['prompt_tokens'] == 6
    assert assessment['execution']['billed_cost'] is None
    assert assessment['groups']['numeric_omission:synthetic']['classification']['false_negative_count'] == 0
    # Already assessed means unchanged artifact, not another chance to tune gold or scores.
    assert evaluate_policy(dataset, validation, policy, tmp_path / 'changed-labels.jsonl') == assessment
    final = tmp_path / 'test'
    execute(dataset, final, **kwargs(calls, split='test', frozen_policy=policy))
    assert len(calls) == 6
    summary = evaluate_policy(dataset, final, policy, labels)
    assert summary['product_quality_gain'] is None and summary['split'] == 'test'
    with pytest.raises(ValueError, match='已被使用'):
        execute(dataset, tmp_path / 'second-test', **kwargs(calls, split='test', frozen_policy=policy))


def test_probability_shape_and_model_revision_must_match(tmp_path):
    dataset, labels = fixture_dataset(tmp_path)
    args = kwargs([])
    missing = tmp_path / 'missing-probabilities'
    execute(dataset, missing, **{**args, 'transport': fake_transport([], False)})
    assert all(o['status'] == 'failed' for o in outcomes(missing).values())
    with pytest.raises(ValueError, match='标签或结果'):
        freeze_policy(dataset, missing, labels, 'construction')
    dev = tmp_path / 'dev'
    execute(dataset, dev, **args)
    policy = policy_file(dataset, freeze_policy(dataset, dev, labels, 'construction'))
    validation = tmp_path / 'validation'
    execute(dataset, validation, **{**args, 'split': 'validation', 'frozen_policy': policy,
                                   'transport': fake_transport([], actual='changed-version')})
    assert all(o['status'] == 'failed' for o in outcomes(validation).values())
    assessed = evaluate_policy(dataset, validation, policy, labels)
    assert assessed['complete_for_frozen_evaluation'] is False
    with pytest.raises(ValueError, match='不完整'):
        execute(dataset, tmp_path / 'test', **kwargs([], split='test', frozen_policy=policy))


def test_materiality_and_conclusion_need_human_not_constructed_labels(tmp_path):
    for task in ('change_materiality', 'conclusion_update'):
        case = make_case(task, {'old_source': 'synthetic A', 'new_source': 'synthetic B'},
            {'run_id': 'r', 'version_id': 'v', 'sources': [], 'exposure': 'public'}, origin='synthetic')
        folder = tmp_path / task
        manifest = freeze_dataset([case], folder)
        label = tmp_path / (task + '.jsonl')
        label.write_text(json.dumps({'kind': 'label', 'source': 'construction', 'case_id': case['case_id'],
            'dataset_id': manifest['dataset_id'], 'truth': 'material' if task == 'change_materiality' else 'update_needed'}) + '\n', encoding='utf-8')
        with pytest.raises(ValueError, match='必须人工标注'):
            construction_labels(folder, label)


def test_log_redaction_happens_before_json_escaping():
    from engine import clean_secret
    key = 'fixture-"quoted"-\\slash'
    value = {'response': {'http_error_body': 'remote echoed ' + key},
             'error': {'detail': ['invalid value: ' + key, {key: 'again ' + key}]}}
    saved = json.loads(json.dumps(clean_secret(value, key)))
    assert saved['response']['http_error_body'] == 'remote echoed [REDACTED]'
    assert saved['error']['detail'] == ['invalid value: [REDACTED]', {'[REDACTED]': 'again [REDACTED]'}]
    assert value['response']['http_error_body'].endswith(key)  # no mutation of source response


def test_public_response_keeps_answer_and_token_counts_not_hidden_reasoning(tmp_path):
    dataset, _ = fixture_dataset(tmp_path)
    base = fake_transport([])
    def respond(*args):
        response = base(*args)
        response['choices'][0]['message']['reasoning_content'] = 'PRIVATE-SYNTHETIC-MARKER'
        response['choices'][0]['message']['reasoning'] = 'PRIVATE-SYNTHETIC-MARKER'
        response['internal_trace'] = 'PRIVATE-SYNTHETIC-MARKER'
        response['usage']['completion_tokens_details'] = {'reasoning_tokens': 1}
        return response
    out = tmp_path / 'run'
    execute(dataset, out, **{**kwargs([]), 'transport': respond})
    log = (out / 'events.jsonl').read_text(encoding='utf-8')
    assert 'PRIVATE-SYNTHETIC-MARKER' not in log
    assert all(o['status'] == 'completed' for o in outcomes(out).values())
    assert next(iter(outcomes(out).values()))['usage']['completion_tokens_details']['reasoning_tokens'] == 1


def test_dev_and_frozen_statistics_share_probability_normalization(tmp_path):
    from governance import _rows, summarize
    dataset, labels = fixture_dataset(tmp_path)
    base = fake_transport([])
    def respond(*args):
        response = base(*args)
        if json.loads(args[1]['messages'][1]['content'])['paragraph'].startswith('事实'):
            response['choices'][0]['message']['content'] = json.dumps({'choice': 'fact',
                'probabilities': {'fact': .51, 'incidental': .50, 'uncertain': 0.0}})
        return response
    out = tmp_path / 'run'
    execute(dataset, out, **{**kwargs([]), 'transport': respond})
    old_entry = score_construction(dataset, out, labels, threshold=.507)['tasks'][0]
    _, rows = _rows(dataset, out, construction_labels(dataset, labels), 'dev')
    new_entry = summarize(rows, .507)
    assert old_entry['classification']['false_negative_count'] == new_entry['classification']['false_negative_count'] == 1
    assert old_entry['positive_class_brier'] == new_entry['probability']['positive_class_brier']
