"""prepare_dataset 行为测试（设计 §3.Q2 / 协议 §6.4、§7）。

零真实模型调用；全部使用合成受限数据（带长而唯一的合成 gold 与合成语料）：
* gold 只进 evaluator-only（0700/0600），question_only 只含允许字段；
* exposure ledger：默认 unknown，命中历史 Pro 题目标 exposed；
* 两份 v2 csv 载荷不一致时关闭失败；
* revision/case key 对行序与换行抖动稳定；
* 原 datasets 目录保持不动。
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments' / 'officeqa_structured'
sys.path.insert(0, str(EXPERIMENT))

import prepare_dataset as pd  # noqa: E402

GOLD_A = 'unobtainium-9931'          # 长且唯一：任何子串泄漏都可检出
GOLD_B = '[vibranium-4477, 0.123]'
QUESTION_A = ('What imaginary metal total did customs collections reach in 1854? '
              'Answer with the codename only.')
QUESTION_B = 'Which fiscal year shows war expenditures of exactly 1921 million imaginary dollars?'


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    import csv
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_document(path: Path, elements: list[dict]) -> None:
    for element in elements:
        element.setdefault('bbox', [{'page_id': 0}])
    path.write_text(json.dumps({'document': {'elements': elements}}), encoding='utf-8')


def synthetic_dataset(root: Path) -> Path:
    """A miniature gated source tree: V2 pair + historical Pro + 3-doc corpus."""
    source = root / 'datasetsrc'
    (source / 'fullcorpus' / 'parsed_corpus' / 'jsons').mkdir(parents=True)
    (source / 'fullcorpus' / 'treasury_bulletins_parsed' / 'transformed').mkdir(parents=True)
    write_document(source / 'fullcorpus' / 'parsed_corpus' / 'jsons' / 'doc_alpha__1854.json', [
        {'id': 0, 'type': 'page_header', 'content': 'TREASURY BULLETIN'},
        {'id': 1, 'type': 'text', 'content': 'Customs collections reached unobtainium levels in 1854.'},
        {'id': 2, 'type': 'table', 'content': '<table><tr><td>Year</td><td>1854</td></tr></table>'},
        {'id': 3, 'type': 'text', 'content': 'Appendix on the second page.', 'bbox': [{'page_id': 1}]},
    ])
    write_document(source / 'fullcorpus' / 'parsed_corpus' / 'jsons' / 'doc_beta__1921.json', [
        {'id': 0, 'type': 'title', 'content': 'Combined Statement 1921'},
        {'id': 1, 'type': 'text', 'content': 'War expenditures nominal imaginary dollars 1921'},
    ])
    # 1-based raw page ids: the adapter must normalize to a 0-based canonical index.
    write_document(source / 'fullcorpus' / 'parsed_corpus' / 'jsons' / 'doc_gamma__1900.json', [
        {'id': 0, 'type': 'title', 'content': 'Receipts 1900', 'bbox': [{'page_id': 1}]},
        {'id': 1, 'type': 'text', 'content': 'Second raw page', 'bbox': [{'page_id': 2}]},
    ])
    for stem in ('doc_alpha__1854', 'doc_beta__1921', 'doc_gamma__1900'):
        (source / 'fullcorpus' / 'treasury_bulletins_parsed' / 'transformed' / f'{stem}.txt').write_text(
            f'{stem} flat text\n', encoding='utf-8')
    fields = ['uid', 'question', 'answer', 'source_docs', 'source_files']
    v2rows = [
        {'uid': '0', 'question': QUESTION_A, 'answer': GOLD_A, 'source_docs': 'corpus_file=doc_alpha__1854.txt',
         'source_files': 'doc_alpha__1854.txt'},
        {'uid': '1', 'question': QUESTION_B, 'answer': GOLD_B, 'source_docs': 'corpus_file=doc_beta__1921.txt',
         'source_files': 'doc_beta__1921.txt; doc_gamma__1900.txt'},
    ]
    write_csv(source / 'officeqa_pro_v2.csv', v2rows, fields)
    write_csv(source / 'officeqa_pro_v2.normalized.csv', list(reversed(v2rows)), fields)  # 行序不同、内容一致
    write_csv(source / 'officeqa_pro.csv', [
        {'uid': '7', 'question': QUESTION_A, 'answer': GOLD_A, 'source_docs': '', 'source_files': 'doc_alpha__1854.txt',
         'difficulty': 'hard'},
        {'uid': '8', 'question': 'An old exposed question that never appears in v2.', 'answer': '42',
         'source_docs': '', 'source_files': 'doc_beta__1921.txt', 'difficulty': 'easy'},
    ], ['uid', 'question', 'answer', 'source_docs', 'source_files', 'difficulty'])
    return source


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    source = synthetic_dataset(tmp_path_factory.mktemp('source'))
    data_root = tmp_path_factory.mktemp('data')
    summary = pd.prepare(source, data_root)
    return source, data_root, summary


def test_prepare_sanitizes_and_isolates_gold(prepared):
    source, data_root, summary = prepared
    questions = [json.loads(line) for line in
                 (data_root / 'question_only' / 'question_only.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(questions) == 2
    for row in questions:
        assert set(row) == set(pd.QUESTION_FIELDS)
        assert row['question'] in (QUESTION_A, QUESTION_B)
    # gold 只在 evaluator-only，且目录 0700、文件 0600（规范化 csv 行序为准）
    gold_dir = data_root / 'evaluator-only' / 'gold'
    gold = {row['uid']: row for row in (json.loads(line) for line in
             (gold_dir / 'officeqa_pro_v2.gold.jsonl').read_text(encoding='utf-8').splitlines())}
    assert gold['0']['answer'] == GOLD_A and gold['1']['answer'] == GOLD_B
    assert gold['1']['source_files'] == ['doc_beta__1921.txt', 'doc_gamma__1900.txt']
    assert stat.S_IMODE(gold_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((gold_dir / 'officeqa_pro_v2.gold.jsonl').stat().st_mode) == 0o600
    assert stat.S_IMODE((data_root / 'evaluator-only').stat().st_mode) == 0o700
    gated = data_root / 'gated'
    assert stat.S_IMODE(gated.stat().st_mode) == 0o700
    assert sorted(path.name for path in gated.iterdir()) == [
        'officeqa_pro.csv', 'officeqa_pro_v2.csv', 'officeqa_pro_v2.normalized.csv']
    # 语料全量进入索引（不用 gold 筛），探针全绿
    assert summary['corpus']['manifest']['documents'] == 3
    assert summary['corpus']['probe_status'] == 'green'


def test_gold_and_source_mapping_never_reach_question_only(prepared):
    _, data_root, _ = prepared
    blobs = [path.read_text(encoding='utf-8', errors='replace')
             for path in (data_root / 'question_only').rglob('*') if path.is_file()]
    joined = '\n'.join(blobs)
    for forbidden in (GOLD_A, GOLD_B, 'vibranium', 'unobtainium', 'doc_alpha__1854', 'doc_beta__1921',
                      'doc_gamma__1900', 'pdf_page_number', 'difficulty'):
        assert forbidden not in joined, forbidden


def test_exposure_ledger_marks_historical_pro_questions(prepared):
    _, data_root, _ = prepared
    ledger = json.loads((data_root / 'question_only' / 'exposure_ledger.json').read_text(encoding='utf-8'))
    by_uid = {entry['uid']: entry for entry in ledger['cases']}
    assert by_uid['0']['exposure'] == 'exposed'  # 同题在历史 Pro v1（uid 7）
    assert 'officeqa-pro-v1 uid 7' in by_uid['0']['basis']
    assert by_uid['1']['exposure'] == 'unknown'
    pool = ledger['historical_exposed_pool']
    assert {entry['uid'] for entry in pool} == {'7', '8'}
    assert all(entry['exposure'] == 'exposed' for entry in pool)
    assert 'answer' not in json.dumps(pool)


def test_case_key_is_revision_uid_and_question_hash(prepared):
    _, data_root, _ = prepared
    ledger = json.loads((data_root / 'question_only' / 'exposure_ledger.json').read_text(encoding='utf-8'))
    for entry in ledger['cases']:
        assert entry['case_key'] == pd.case_key(ledger['revision'], entry['uid'], entry['question_sha256'])
    # NFKC/空白折叠后同题同哈希
    assert pd.question_sha256('What  was\r\nthe\ttotal?') == pd.question_sha256('What was the total?')


def test_revision_is_stable_across_row_order_and_line_endings(prepared):
    source, data_root, _ = prepared
    first = json.loads((data_root / 'question_only' / 'exposure_ledger.json').read_text(encoding='utf-8'))['revision']
    csv_path = source / 'officeqa_pro_v2.normalized.csv'
    csv_path.write_text(csv_path.read_text(encoding='utf-8').replace('\n', '\r\n'), encoding='utf-8')
    second_root = data_root.parent / 'data2'
    pd.prepare(source, second_root, skip_corpus=True)
    second = json.loads((second_root / 'question_only' / 'exposure_ledger.json').read_text(encoding='utf-8'))
    assert second['revision'] == first
    first_q = [json.loads(line) for line in
               (data_root / 'question_only' / 'question_only.jsonl').read_text(encoding='utf-8').splitlines()]
    second_q = [json.loads(line) for line in
                (second_root / 'question_only' / 'question_only.jsonl').read_text(encoding='utf-8').splitlines()]
    assert {(row['uid'], row['case_key']) for row in first_q} == {(row['uid'], row['case_key']) for row in second_q}


def test_v2_payload_disagreement_fails_closed(tmp_path):
    source = synthetic_dataset(tmp_path)
    rows = (source / 'officeqa_pro_v2.csv').read_text(encoding='utf-8').replace(GOLD_A, 'drifted-gold')
    (source / 'officeqa_pro_v2.csv').write_text(rows, encoding='utf-8')
    with pytest.raises(pd.PrepareError, match='disagrees'):
        pd.prepare(source, tmp_path / 'data', skip_corpus=True)


def test_prepare_leaves_the_source_directory_untouched(prepared):
    source, data_root, _ = prepared
    before = {path: (path.stat().st_mtime_ns, path.read_bytes())
              for path in sorted(source.rglob('*')) if path.is_file()}
    pd.prepare(source, data_root.parent / 'data3', skip_corpus=True)
    after = {path: (path.stat().st_mtime_ns, path.read_bytes())
             for path in sorted(source.rglob('*')) if path.is_file()}
    assert before == after


def test_source_files_parsing_handles_semicolons_and_newlines():
    assert pd.split_names('a.txt; b.txt\nc.txt\n\nd.txt') == ('a.txt', 'b.txt', 'c.txt', 'd.txt')
    assert pd.split_names('') == ()
