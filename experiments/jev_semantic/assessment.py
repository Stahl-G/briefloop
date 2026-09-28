from collections import Counter, defaultdict
from pathlib import Path

from dataset import digest, json_lines, load_dataset, read_json
from engine import POLICY, TASKS, outcomes, positive_probability, priority


def labels_for(dataset, annotations, split=None):
    manifest, cases = load_dataset(dataset)
    known = {c['case_id']: c for c in cases if split is None or c['split'] == split}
    latest = {}
    for row in json_lines(annotations):
        identity = row.get('case_id')
        if row.get('kind') != 'label' or identity not in known or row.get('source') != 'human':
            continue
        if row.get('dataset_id') != manifest['dataset_id'] or row.get('input_hash') != manifest['cases'][identity]:
            raise ValueError('人工标签与数据集身份不符')
        if row.get('choice') not in TASKS[known[identity]['task']]['criteria'] or not row.get('reason', '').strip():
            raise ValueError('人工标签缺少有效类别或依据')
        latest[identity, row['reviewer']] = row
    grouped = defaultdict(list)
    for (identity, _), row in latest.items():
        grouped[identity].append(row)
    labels = {}
    for identity, rows in grouped.items():
        labels[identity] = (rows[-1] if len({r['choice'] for r in rows}) == 1 else
                            {'choice': None, 'disagreement': True})
    return labels


def metrics(rows):
    if not rows:
        return {'n': 0, 'precision': None, 'recall': None, 'false_positive_count': 0}
    tp = sum(gold and pred for gold, pred in rows)
    fp = sum(not gold and pred for gold, pred in rows)
    fn = sum(gold and not pred for gold, pred in rows)
    return {'n': len(rows), 'true_positive_count': tp, 'false_positive_count': fp,
            'false_negative_count': fn, 'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None}


def auc(pairs):
    pos = [score for score, gold in pairs if gold]
    neg = [score for score, gold in pairs if not gold]
    if not pos or not neg:
        return None
    wins = sum(1 if a > b else 0.5 if a == b else 0 for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def construction_labels(dataset, truth_path):
    manifest, cases = load_dataset(dataset)
    known = {c['case_id']: c for c in cases}
    labels = {}
    for row in json_lines(truth_path):
        identity = row.get('case_id')
        if row.get('kind') != 'label' or row.get('source') != 'construction' or identity not in known:
            continue
        if row.get('dataset_id') != manifest['dataset_id']:
            raise ValueError('构造标签与数据集身份不符')
        if known[identity]['task'] != 'numeric_omission':
            raise ValueError('材料性和结论更新必须人工标注，不能用构造标签')
        if known[identity]['origin'] != 'synthetic':
            raise ValueError('构造标签只允许用于明确标记为合成的对象')
        if row.get('truth') not in TASKS[known[identity]['task']]['criteria']:
            raise ValueError('构造标签类别不在任务定义中')
        labels[identity] = row
    return labels


def score_construction(dataset, run_folder, truth_path, threshold=None):
    manifest, cases = load_dataset(dataset)
    protocol = read_json(Path(run_folder) / 'protocol.json')
    if protocol['dataset_id'] != manifest['dataset_id']:
        raise ValueError('运行与数据集身份不符')
    if protocol['split'] != 'dev':
        raise ValueError('留出集必须使用 evaluate-policy 与已冻结阈值，不能试探阈值')
    if threshold is not None and not 0 <= threshold <= 1:
        raise ValueError('阈值必须在 0 到 1 之间')
    labels = construction_labels(dataset, truth_path)
    output = outcomes(run_folder)
    rows = []
    for task in sorted({c['task'] for c in cases}):
        spec = TASKS[task]
        subset = [c for c in cases if c['task'] == task and c['split'] == protocol['split']]
        classified, pairs, brier, via_code = [], [], [], 0
        unlabelled = failed = 0
        for c in subset:
            label = labels.get(c['case_id'])
            outcome = output.get(c['case_id'])
            if outcome and outcome['status'] == 'completed' and outcome.get('via') == 'code_prefilter':
                via_code += 1
            if not label:
                unlabelled += 1
                continue
            gold = label['truth'] in spec['positive']
            if not outcome or outcome['status'] != 'completed':
                failed += 1
                continue
            answer = outcome['answer']
            probs = answer.get('probabilities')
            score = positive_probability(answer, task) if probs else float(answer['choice'] in spec['positive'])
            predicted = score >= threshold if threshold is not None else answer['choice'] in spec['positive']
            classified.append((gold, predicted))
            pairs.append((score, gold))
            if probs:
                brier.append((score - float(gold)) ** 2)
        rows.append({'task': task, 'objects': len(subset), 'labelled': len(subset) - unlabelled,
            'via_code_prefilter': via_code, 'not_completed': failed,
            'classification': metrics(classified), 'auc': auc(pairs),
            'positive_class_brier': sum(brier) / len(brier) if brier else None})
    return {'dataset_id': manifest['dataset_id'], 'run_id': protocol['run_id'],
        'provider': protocol['provider'], 'split': protocol['split'],
        'threshold': threshold, 'label_source': 'construction',
        'tasks': rows,
        'interpretation': '构造标签只覆盖模板确定性场景，结果是能力上界，不是自然分布估计'}


def evaluate(dataset, run_folders, annotations, budget=5):
    if budget < 1:
        raise ValueError('提示预算必须为正数')
    manifest, cases = load_dataset(dataset)
    labels = labels_for(dataset, annotations)
    comparison = []
    partitions = set()
    common_questions = None
    for folder in run_folders:
        protocol = read_json(Path(folder) / 'protocol.json')
        if protocol['dataset_id'] != manifest['dataset_id']:
            raise ValueError('不同数据集不能作同题对照')
        if protocol['split'] != 'dev':
            raise ValueError('留出集必须使用 evaluate-policy 与已冻结阈值')
        if budget != protocol['policy']['review_budget_per_report']:
            raise ValueError('提示预算必须与运行前冻结的策略一致')
        partitions.add(protocol['split'])
        qhash = digest(protocol['questions'])
        if common_questions is not None and qhash != common_questions:
            raise ValueError('不同问题定义不能作同题对照')
        common_questions = qhash
        output = outcomes(folder)
        selected = [c for c in cases if c['split'] == protocol['split']]
        rows = []
        for task, origin in sorted({(c['task'], c['origin']) for c in selected}):
            subset = [c for c in selected if c['task'] == task and c['origin'] == origin]
            spec = TASKS[task]
            classified, action, brier, ranks, confusion = [], [], [], defaultdict(list), Counter()
            unknown_labels = failed = unlabelled = abstentions = 0
            for c in subset:
                label = labels.get(c['case_id'])
                outcome = output.get(c['case_id'])
                if not outcome or outcome['status'] != 'completed':
                    failed += 1
                elif outcome['answer']['choice'] == spec['unknown']:
                    abstentions += 1
                if not label:
                    unlabelled += 1
                    continue
                if not label.get('choice') or label['choice'] == spec['unknown']:
                    unknown_labels += 1
                    continue
                gold = label['choice'] in spec['positive']
                actionable = gold and (task != 'numeric_omission' or c['observed'].get('verification') != 'checked')
                if outcome and outcome['status'] == 'completed':
                    answer = outcome['answer']; choice = answer['choice']; probs = answer.get('probabilities')
                    predicted = choice in spec['positive']
                    classified.append((gold, predicted))
                    action.append((actionable, predicted and (task != 'numeric_omission' or c['observed'].get('verification') != 'checked')))
                    confusion[label['choice'], choice] += 1
                    score = positive_probability(answer, task) if probs else float(predicted)
                    if probs:
                        brier.append((score - float(gold)) ** 2)
                    if task == 'numeric_omission' and c['observed'].get('verification') == 'checked':
                        score = -1
                    if c.get('safety', {}).get('must_review'):
                        score = 2
                    ranks[c['provenance']['run_id']].append((score, c['case_id'], actionable))
            top = [entry for items in ranks.values() for entry in sorted(items, key=lambda x: (-x[0], x[1]))[:budget]]
            rows.append({'task': task, 'origin': origin, 'objects': len(subset), 'unlabelled': unlabelled,
                'unknown_or_disputed_labels': unknown_labels, 'not_completed': failed, 'model_abstentions': abstentions,
                'classification': metrics(classified), 'actionable_hints': metrics(action),
                'positive_class_brier': sum(brier) / len(brier) if brier else None,
                'confusion': [{'truth': a, 'prediction': b, 'count': n} for (a, b), n in sorted(confusion.items())],
                'top_k_per_report': {'k': budget, 'selected': len(top), 'labelled_actionable': sum(x[2] for x in top),
                    'scope': '仅已标注且成功执行的对象；覆盖不足时不能比较整体产品收益'}})
        events = json_lines(Path(folder) / 'events.jsonl')
        comparison.append({'provider': protocol['provider'], 'requested_model': protocol['model'],
            'actual_models': sorted({e['model'] for e in events if e.get('model')}),
            'run_id': protocol['run_id'], 'split': protocol['split'],
            'attempts': sum(e['event'] == 'attempt_started' for e in events),
            'failed_attempts': sum(e['event'] == 'result' and e['status'] == 'failed' for e in events), 'tasks': rows})
    if len(partitions) > 1:
        raise ValueError('不同数据划分不能作同题对照')
    return {'dataset_id': manifest['dataset_id'], 'comparisons': comparison,
        'interpretation': '探索性局部判断实验；无人工标签则不能评价判断质量。合成与自然材料分别汇报。',
        'human_time_savings': None, 'product_quality_gain': None,
        'limitations': ['未减少 Evaluator 范围，未测正式交付质量或人工净节省时间',
                       '离散对照不伪造概率；Brier 仅对带概率的预测计算',
                       '阈值和提示预算须在留出集调用前冻结；分组不足时不宣称独立验证']}


def review_queue(dataset, run_folder, annotations=None):
    manifest, cases = load_dataset(dataset)
    protocol = read_json(Path(run_folder) / 'protocol.json')
    if protocol['dataset_id'] != manifest['dataset_id'] or protocol['policy'] != POLICY:
        raise ValueError('复核清单与数据或策略版本不符')
    output = outcomes(run_folder)
    actions = {}
    for row in json_lines(annotations) if annotations else []:
        if (row.get('kind') == 'review' and row.get('run_id') == protocol['run_id']
                and row.get('dataset_id') == manifest['dataset_id']
                and row.get('input_hash') == manifest['cases'].get(row.get('case_id'))):
            actions[row['case_id']] = row
    return [{'case_id': c['case_id'], 'task': c['task'], 'version_id': c['provenance']['version_id'],
             'location': c['provenance'].get('body_range') or c['provenance'].get('block_id'),
             'hint': priority(c, output.get(c['case_id'])), 'execution_status': output.get(c['case_id'], {}).get('status', 'not_run'),
             'observed': c['observed'], 'judgment': output.get(c['case_id'], {}).get('answer'),
             'consumer': '独立复核者', 'action_status': actions.get(c['case_id'], {}).get('choice', 'pending'),
             'disposition': actions.get(c['case_id']), 'affects_release': False}
            for c in cases if c['split'] == protocol['split']]
