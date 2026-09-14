"""analyze_results 行为测试（协议 §9.3 配对统计 / §9.4 结果模板）。

合成分数（不触碰任何真实 gold）：
* exact McNemar 与教科书二项值一致；无不一致配对时返回 None 而不是假 p=1；
* 配对 bootstrap 按题聚类、种子可复现，区间含点估计；
* analyze→render 全链：从 scores.jsonl/summary/frozen manifest 读入，
  产出 §9.4 模板字段（整数题数 + 百分比 + 区间 + Not established 行）；
* dry-run 如实标注 stub 语义。
"""
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments' / 'officeqa_structured'
sys.path.insert(0, str(EXPERIMENT))

import analyze_results as ar  # noqa: E402


def test_exact_mcnemar_matches_binomial_values():
    # b=1,c=0：2*(C(1,0)+C(1,1))/2^1 = 2.0 → 截到 1.0
    assert ar.exact_mcnemar(1, 0) == 1.0
    # b=8,c=1：2*(C(9,0)+C(9,1))/2^9 = 20/512
    assert ar.exact_mcnemar(8, 1) == pytest.approx(20 / 512)
    # 对称性与None
    assert ar.exact_mcnemar(3, 8) == ar.exact_mcnemar(8, 3)
    assert ar.exact_mcnemar(0, 0) is None
    # 与逐项二项尾概率一致
    n, k = 9, 1
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    assert ar.exact_mcnemar(8, 1) == pytest.approx(min(1.0, 2 * tail))


def test_paired_bootstrap_is_seeded_and_covers_point_estimate():
    pairs = [(1, 0)] * 8 + [(0, 1)] * 2 + [(1, 1)] * 10
    first = ar.paired_bootstrap(pairs, samples=2000, seed=42)
    second = ar.paired_bootstrap(pairs, samples=2000, seed=42)
    assert first == second  # 同种子可复现（协议 §9.3 保存种子）
    assert first['delta'] == pytest.approx((18 - 12) / 20, abs=1e-9)
    assert first['delta_correct_counts'] == {'b': 18, 'a': 12}
    assert first['ci95'][0] <= first['delta'] <= first['ci95'][1]
    other = ar.paired_bootstrap(pairs, samples=2000, seed=43)
    assert other['ci95'] != first['ci95'] or other is not first  # 种子改变重抽样
    with pytest.raises(ar.AnalysisError):
        ar.paired_bootstrap([], samples=1000, seed=1)
    with pytest.raises(ar.AnalysisError):
        ar.paired_bootstrap(pairs, samples=10, seed=1)


def scores_tree(tmp_path: Path, *, dry_run: bool = True) -> Path:
    data_root = tmp_path / 'data'
    scores_root = data_root / 'evaluator-only' / 'scores' / 'r1'
    scores_root.mkdir(parents=True)
    rows = []
    for index in range(20):
        b = 1 if index < 11 else 0
        a = 1 if index < 8 else 0
        for arm, correct in (('A', a), ('B', b)):
            rows.append({'schema_version': 'officeqa.scores.v1', 'run_label': 'r1', 'arm': arm,
                         'case_key': f'key{index:02d}', 'uid': str(index),
                         'score': float(correct), 'outcome': 'answered',
                         'answer_sha256': 'x', 'reason': None})
    rows[3]['outcome'] = 'abstained'   # A 侧一个弃答（分数 0）
    rows.append({'schema_version': 'officeqa.scores.v1', 'run_label': 'r1', 'arm': 'A',
                 'case_key': 'key99', 'uid': '99', 'score': 0.0, 'outcome': 'missing',
                 'answer_sha256': None, 'reason': 'episode did not run'})
    (scores_root / 'scores.jsonl').write_text(
        '\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')
    (scores_root / 'summary.json').write_text(json.dumps({
        'schema_version': 'officeqa.score_summary.v1', 'run_label': 'r1',
        'protocol_id': 'BL-OQA-SR-v1.0', 'experiment_id': 'briefloop-officeqa-structured',
        'dry_run': dry_run, 'arms': {
            'A': {'N': 21, 'correct': 8, 'accuracy': round(8 / 21, 4), 'valid_answer_rate': 0.9524,
                  'abstained': 1, 'format_error': 0, 'missing': 1},
            'B': {'N': 20, 'correct': 11, 'accuracy': 0.55, 'valid_answer_rate': 1.0,
                  'abstained': 0, 'format_error': 0, 'missing': 0}},
    }, ensure_ascii=False), encoding='utf-8')
    frozen = data_root / 'evaluator-only' / 'frozen' / 'r1'
    frozen.mkdir(parents=True)
    (frozen / 'manifest.json').write_text(json.dumps({'dry_run': dry_run, 'predictions': 41}),
                                          encoding='utf-8')
    return data_root


def test_analyze_and_render_produce_the_94_template(tmp_path):
    data_root = scores_tree(tmp_path)
    results = ar.analyze(data_root, 'r1', samples=2000, seed=7)
    # 主指标按 0/1 正确数计算，整数题数与百分比并存
    assert results['arms']['A']['correct'] == 8 and results['arms']['A']['N'] == 21
    assert results['arms']['B']['correct'] == 11 and results['arms']['B']['N'] == 20
    assert results['paired']['available'] is True and results['paired']['n_pairs'] == 20
    assert results['paired']['b_wrong_to_right'] == 3 and results['paired']['b_right_to_wrong'] == 0
    # b=3,c=0：exact McNemar p = 2/2^3*... = 2*(1)/8=0.25
    assert results['paired']['exact_mcnemar_p'] == pytest.approx(0.25)
    bootstrap = results['paired']['bootstrap']
    assert bootstrap['delta'] == pytest.approx(11 / 20 - 8 / 20)
    assert bootstrap['ci95'][0] <= bootstrap['delta'] <= bootstrap['ci95'][1]
    # 无 run_index：过程列如实留空而不是编造
    assert results['arms']['A']['end_to_end_complete'] is None
    assert results['arms']['A']['usage_complete'] is None
    markdown = ar.render_template(results)
    for needle in ('Paired Delta:', 'B wrong→right: 3 ; B right→wrong: 0',
                   'exact McNemar p = 0.2500', 'Native control', 'BriefLoop QA',
                   'Not established: report quality, Word formatting, long-term learning benefits.',
                   'dry-run stub'):
        assert needle in markdown, needle
    assert '8 / 21' in markdown and '11 / 20' in markdown  # 整数题数
    assert results['dry_run'] is True and 'dry-run stub' in json.dumps(results['notes'], ensure_ascii=False)


def test_cli_writes_private_results(tmp_path):
    data_root = scores_tree(tmp_path)
    assert ar.main(['--data-root', str(data_root), '--run-label', 'r1',
                    '--samples', '2000', '--seed', '7']) == 0
    out = data_root / 'evaluator-only' / 'scores' / 'r1'
    results = json.loads((out / 'results.json').read_text(encoding='utf-8'))
    assert results['schema_version'] == 'officeqa.results.v1'
    assert results['paired']['bootstrap']['seed'] == 7
    assert (out / 'results.md').is_file()
    import stat as stat_module
    assert stat_module.S_IMODE((out / 'results.json').stat().st_mode) & 0o177 == 0  # owner-only
    assert ar.main(['--data-root', str(tmp_path / 'missing'), '--run-label', 'nope']) == 2
