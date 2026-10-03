"""Local experiment registration; no production decision or model invocation here."""
from collections import Counter
from datetime import datetime
from pathlib import Path
import math

from dataset import digest, json_lines, load_dataset, now, read_json, save_new
from engine import TASKS, outcomes, positive_probability, validate_answer
from assessment import construction_labels, labels_for, metrics
from wikiskill.k4_lock import workspace_lock

SCHEMA = 'semantic-calibration.v1'
IDENTITY_FIELDS = ('dataset_id', 'provider', 'model', 'endpoint', 'questions', 'policy',
                   'prefilter', 'response_shape', 'response_format', 'code_hash', 'script_hashes', 'max_attempts',
                   'timeout_seconds_per_attempt', 'max_request_bytes', 'allow_private_external')


def registry(dataset):
    return Path(dataset) / '.semantic-governance'


def identity(protocol):
    if any(key not in protocol for key in IDENTITY_FIELDS):
        raise ValueError('旧运行没有完整冻结身份；保留原记录，新策略从新版 dev 协议开始')
    return {key: protocol[key] for key in IDENTITY_FIELDS}


def arm_id(protocol):
    # A changed prompt or code must not grant a second look at one arm's heldout data.
    return digest({key: protocol[key] for key in ('provider', 'model', 'endpoint', 'response_shape', 'response_format', 'prefilter')})


def _policy_path(dataset, policy_id):
    return registry(dataset) / ('policy-' + policy_id + '.json')


def load_policy(dataset, path):
    policy = read_json(path)
    if (policy.get('schema') != SCHEMA or policy.get('policy_id') !=
            digest({k: v for k, v in policy.items() if k != 'policy_id'})):
        raise ValueError('冻结策略身份不符')
    registered = _policy_path(dataset, policy['policy_id'])
    if not registered.exists() or read_json(registered) != policy:
        raise ValueError('策略没有在这个数据集登记；不可事后替换')
    return policy


def _reservation_path(dataset, protocol):
    return registry(dataset) / (protocol['split'] + '-' + arm_id(protocol) + '.json')


def guard_run(dataset, protocol, out, policy):
    root = registry(dataset)
    with workspace_lock(root):
        if protocol['split'] == 'dev':
            if policy is not None:
                raise ValueError('dev 不使用留出集策略；请先运行开发集，再冻结策略')
            if any(root.glob('validation-*.json')) or any(root.glob('test-*.json')):
                raise ValueError('留出集已启用，此数据集不再开放开发调参；另立新数据集协议')
            return
        if policy is None or identity(protocol) != policy['run_identity']:
            raise ValueError('留出集需要匹配数据、问题、模型、代码和参数的冻结策略')
        _, cases = load_dataset(dataset)
        required = {c['task'] + ':' + c['origin'] for c in cases if c['split'] == protocol['split']}
        if not required.issubset(policy['groups']):
            raise ValueError('留出集含开发集中未完成标注及冻结的任务/材料类型')
        path = _reservation_path(dataset, protocol)
        reservation = {'policy_id': policy['policy_id'], 'run_id': protocol['run_id'],
                       'output_directory': str(Path(out).resolve()), 'split': protocol['split']}
        if path.exists():
            if read_json(path) != reservation:
                raise ValueError('该对照臂的留出集已被使用；只允许原协议、原目录续跑')
            return
        if protocol['split'] == 'test':
            validation = root / ('validation-' + arm_id(protocol) + '.json')
            evaluation = root / ('evaluation-validation-' + arm_id(protocol) + '.json')
            if not validation.exists() or read_json(validation)['policy_id'] != policy['policy_id'] or not evaluation.exists():
                raise ValueError('test 封存：先完成同一冻结策略的单次 validation 评价')
            summary = read_json(evaluation)
            if summary['policy_id'] != policy['policy_id'] or not summary['complete_for_frozen_evaluation']:
                raise ValueError('validation 执行或标注不完整，test 继续封存')
        # Reserve before the first request. Errors do not silently refund a heldout look.
        save_new(path, reservation)


def _labels(dataset, path, source, split):
    if source == 'human':
        return labels_for(dataset, path, split)
    if source == 'construction':
        return construction_labels(dataset, path)
    raise ValueError('标签来源必须明确为 human 或 construction')


def _rows(dataset, run_folder, labels, split):
    manifest, cases = load_dataset(dataset)
    protocol = read_json(Path(run_folder) / 'protocol.json')
    if protocol['dataset_id'] != manifest['dataset_id'] or protocol['split'] != split:
        raise ValueError('运行必须来自指定数据集和 split')
    if protocol['run_id'] != digest({k: v for k, v in protocol.items() if k != 'run_id'}):
        raise ValueError('运行协议身份不符')
    output = outcomes(run_folder)
    rows = []
    for case in cases:
        if case['split'] != split:
            continue
        result = output.get(case['case_id'])
        if result:
            if result.get('run_id') != protocol['run_id'] or result.get('input_hash') != manifest['cases'][case['case_id']]:
                raise ValueError('结果与冻结输入/运行身份不符')
            if result['status'] == 'completed':
                validate_answer(result['answer'], case['task'])
        label = labels.get(case['case_id'])
        choice = (label.get('choice') if label.get('source') == 'human' else label.get('truth')) if label else None
        gold = None if choice is None or choice == TASKS[case['task']]['unknown'] else choice in TASKS[case['task']]['positive']
        probabilities = result.get('answer', {}).get('probabilities') if result and result['status'] == 'completed' else None
        score = positive_probability(result['answer'], case['task']) if probabilities else None
        rows.append({'case': case, 'result': result, 'label': label, 'gold': gold, 'score': score})
    return protocol, rows


def _classified(row, threshold):
    result = row['result']
    if not result or result['status'] != 'completed' or row['gold'] is None:
        return None
    if threshold is not None and row['score'] is not None:
        return row['score'] >= threshold
    return result['answer']['choice'] in TASKS[row['case']['task']]['positive']


def probability_metrics(rows):
    pairs = [(r['score'], r['gold']) for r in rows if r['score'] is not None and r['gold'] is not None]
    bins = []
    for i in range(10):
        subset = [(p, y) for p, y in pairs if min(int(p * 10), 9) == i]
        bins.append({'lower': i / 10, 'upper': (i + 1) / 10, 'n': len(subset),
                     'mean_positive_probability': sum(p for p, _ in subset) / len(subset) if subset else None,
                     'positive_fraction': sum(y for _, y in subset) / len(subset) if subset else None})
    return {'n': len(pairs), 'positive_class_brier': sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else None,
            'ece_10_equal_width_bins': sum(b['n'] * abs(b['mean_positive_probability'] - b['positive_fraction'])
                                         for b in bins if b['n']) / len(pairs) if pairs else None,
            'bins': bins, 'probability_histogram': [{'probability': p, 'count': n} for p, n in sorted(Counter(p for p, _ in pairs).items())]}


def summarize(rows, threshold):
    pairs = [(r['gold'], _classified(r, threshold)) for r in rows if _classified(r, threshold) is not None]
    classified = metrics(pairs)
    tn = sum(not gold and not pred for gold, pred in pairs)
    fp, fn = classified['false_positive_count'], classified.get('false_negative_count', 0)
    tp = classified.get('true_positive_count', 0)
    classified.update(true_negative_count=tn, false_negative_count=fn,
                      balanced_accuracy=((tp / (tp + fn) + tn / (tn + fp)) / 2 if tp + fn and tn + fp else None))
    return {'objects': len(rows), 'threshold': threshold, 'classification': classified,
            'classification_scope': '模型判断与确定性前置过滤共同构成的实验管线',
            'model_only_classification': metrics([(r['gold'], _classified(r, threshold)) for r in rows
                if _classified(r, threshold) is not None and r['result'].get('via') != 'code_prefilter']),
            'unlabelled_or_disputed_or_unknown': sum(r['gold'] is None for r in rows),
            'not_completed': sum(not r['result'] or r['result']['status'] != 'completed' for r in rows),
            'via_code_prefilter': sum(bool(r['result'] and r['result'].get('via') == 'code_prefilter') for r in rows),
            'model_abstentions': sum(bool(r['result'] and r['result'].get('answer') and
                r['result']['answer']['choice'] == TASKS[r['case']['task']]['unknown']) for r in rows),
            'probability': probability_metrics(rows)}


def _choose_threshold(rows):
    paired = [r for r in rows if r['score'] is not None and r['gold'] is not None]
    if {r['gold'] for r in paired} != {False, True}:
        raise ValueError('概率阈值拟合需要开发集同时有正负标签，不能从无标签/单类数据推断')
    values = sorted({r['score'] for r in paired})
    candidates = {0.0, 1.0, *values, *(math.nextafter(p, 1.0) for p in values)}
    # Predeclared objective; ties favor the higher threshold. This only ranks review hints.
    return max(candidates, key=lambda t: (summarize(paired, t)['classification']['balanced_accuracy'], t))


def execution_metrics(run_folder):
    events = json_lines(Path(run_folder) / 'events.jsonl')
    attempts = [e for e in events if e['event'] == 'attempt_started']
    results = [e for e in events if e['event'] == 'result' and e.get('attempt_id')]
    totals = Counter()
    def collect(value, prefix=''):
        for key, item in value.items():
            name = prefix + key
            if isinstance(item, dict):
                collect(item, name + '.')
            elif isinstance(item, (float, int)) and not isinstance(item, bool) and math.isfinite(item):
                totals[name] += item
    for result in results:
        if isinstance(result.get('usage'), dict):
            collect(result['usage'])
    wall = (datetime.fromisoformat(events[-1]['at']) - datetime.fromisoformat(events[0]['at'])).total_seconds() if events else None
    return {'requests_started': len(attempts), 'failed_attempts': sum(e['status'] != 'completed' for e in results),
            'attempts_without_result': len({e['attempt_id'] for e in attempts} - {e['attempt_id'] for e in results}),
            'results_without_usage': sum(not isinstance(e.get('usage'), dict) for e in results),
            'usage_field_totals': dict(sorted(totals.items())),
            'actual_models_including_failures': sorted({e['model'] for e in results if e.get('model')}),
            'request_latency_sum_seconds': sum(e.get('latency_s', 0) for e in results),
            'recorded_wall_seconds': wall, 'billed_cost': None,
            'usage_note': '按提供方原字段累加，嵌套项不是可相加的独立总量；缺失 usage 不作零消耗，无账单不推算费用'}


def freeze_policy(dataset, run_folder, label_path, source='human'):
    root = registry(dataset)
    with workspace_lock(Path(run_folder)), workspace_lock(root):
        if any(root.glob('validation-*.json')) or any(root.glob('test-*.json')):
            raise ValueError('已查看留出集，不能再拟合或登记新阈值')
        labels = _labels(dataset, label_path, source, 'dev')
        protocol, rows = _rows(dataset, run_folder, labels, 'dev')
        if not rows:
            raise ValueError('开发集没有样本')
        groups = {}
        for group in sorted({r['case']['task'] + ':' + r['case']['origin'] for r in rows}):
            subset = [r for r in rows if r['case']['task'] + ':' + r['case']['origin'] == group]
            if not any(r['gold'] is not None and r['result'] and r['result']['status'] == 'completed' for r in subset):
                raise ValueError('任务缺少可用开发标签或结果：' + group)
            threshold = _choose_threshold(subset) if protocol['response_shape'] == 'choice+probabilities' else None
            groups[group] = summarize(subset, threshold)
        actual = sorted({r['result']['model'] for r in rows if r['result'] and r['result']['status'] == 'completed'
                         and r['result'].get('via') != 'code_prefilter'})
        if len(actual) != 1:
            raise ValueError('冻结需要单一实际模型身份；混用模型或只有代码过滤结果不能拟合')
        policy = {'schema': SCHEMA, 'created': now(), 'dataset_id': protocol['dataset_id'],
                  'fit_split': 'dev', 'run_identity': identity(protocol), 'dev_run_id': protocol['run_id'],
                  'dev_results_hash': digest(json_lines(Path(run_folder) / 'events.jsonl')),
                  'dev_execution': execution_metrics(run_folder),
                  'dev_labels_hash': digest({r['case']['case_id']: r['label'] for r in rows}),
                  'label_source': source, 'actual_models': actual, 'groups': groups,
                  'threshold_objective': 'maximize_dev_binary_balanced_accuracy_tie_higher_threshold',
                  'calibration_code_hash': digest(Path(__file__).read_bytes()),
                  'probability_rounding': '验证分布总和后归一到 1；不把离散类别伪造为概率',
                  'probability_alone_may_release': False,
                  'model_identity_limit': '实际身份取 API 返回值；别名或服务端同名更新不能证明权重版本已固定',
                  'synthetic_limit': '合成构造标签仅用于数字定位场景，不代表自然数据质量或人工真值'}
        policy['policy_id'] = digest(policy)
        save_new(_policy_path(dataset, policy['policy_id']), policy)
        return policy


def evaluate_policy(dataset, run_folder, policy_path, label_path):
    policy = load_policy(dataset, policy_path)
    protocol = read_json(Path(run_folder) / 'protocol.json')
    if protocol['split'] not in ('validation', 'test') or identity(protocol) != policy['run_identity']:
        raise ValueError('必须评价同一冻结协议的 validation/test 运行')
    if protocol.get('frozen_policy_id') != policy['policy_id']:
        raise ValueError('运行未绑定这个冻结策略')
    # The same original directory may be resumed; a new directory never creates a new look.
    guard_run(dataset, protocol, run_folder, policy)
    with workspace_lock(Path(run_folder)), workspace_lock(registry(dataset)):
        target = registry(dataset) / ('evaluation-' + protocol['split'] + '-' + arm_id(protocol) + '.json')
        if target.exists():
            # Return the original immutable assessment without consuming changed labels/results.
            return read_json(target)
        labels = _labels(dataset, label_path, policy['label_source'], protocol['split'])
        _, rows = _rows(dataset, run_folder, labels, protocol['split'])
        result_models = {r['result']['model'] for r in rows if r['result'] and r['result']['status'] == 'completed'
                         and r['result'].get('via') != 'code_prefilter'}
        if not result_models.issubset(policy['actual_models']):
            raise ValueError('实际模型身份与冻结开发集不同')
        groups = {key: summarize([r for r in rows if r['case']['task'] + ':' + r['case']['origin'] == key],
                                 group['threshold']) for key, group in policy['groups'].items()}
        result = {'schema': 'semantic-heldout-evaluation.v1', 'created': now(), 'policy_id': policy['policy_id'],
                  'dataset_id': policy['dataset_id'], 'run_id': protocol['run_id'], 'split': protocol['split'],
                  'groups': groups, 'label_source': policy['label_source'],
                  'execution': execution_metrics(run_folder),
                  'labels_hash': digest({r['case']['case_id']: r['label'] for r in rows}),
                  'results_hash': digest(json_lines(Path(run_folder) / 'events.jsonl')),
                  'complete_for_frozen_evaluation': bool(rows) and all(r['label'] and not r['label'].get('disagreement') and r['result'] and r['result']['status'] == 'completed' for r in rows),
                  'probability_alone_may_release': False, 'product_quality_gain': None,
                  'human_time_savings': None, 'limitation': '局部标签与模型声明概率；不证明产品收益或正式交付通过'}
        save_new(target, result)
        return result
