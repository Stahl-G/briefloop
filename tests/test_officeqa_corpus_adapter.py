"""corpus_adapter 行为测试（设计 §3.Q2 / 协议 §6.1）。

零模型调用、零题目数据：探针与检索只用语料自身的词。覆盖——
* build→probe 全绿；元素=行、page 规范化为 0-based（原文 1-based 也归一）；
* search/read/page 与证据 locator（1-based 行、0-based page_index）一致；
* FTS 注入被引号中和；纯标点查询关闭失败；
* accept 走产品真实接纳路径（Store.add_source + attach_source），正文与投影逐行对齐；
* query-blind：模块不存在读题面/gold 的代码路径；staged 文件缺失时探针变红。
"""
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments' / 'officeqa_structured'
sys.path.insert(0, str(EXPERIMENT))

import corpus_adapter as ca  # noqa: E402


def write_document(path: Path, elements: list[dict]) -> None:
    for element in elements:
        element.setdefault('bbox', [{'page_id': 0}])
    path.write_text(json.dumps({'document': {'elements': elements}}), encoding='utf-8')


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp('corpus')
    source = root / 'fullcorpus'
    jsons = source / 'parsed_corpus' / 'jsons'
    txts = source / 'treasury_bulletins_parsed' / 'transformed'
    jsons.mkdir(parents=True)
    txts.mkdir(parents=True)
    write_document(jsons / 'doc_alpha__1854.json', [
        {'id': 0, 'type': 'page_header', 'content': 'TREASURY BULLETIN'},
        {'id': 1, 'type': 'text', 'content': 'Customs collections reached 1,234 dollars in 1854.'},
        {'id': 2, 'type': 'table', 'content': '<table><tr><th>Year</th><td>1854</td></tr></table>'},
        {'id': 3, 'type': 'text', 'content': 'Appendix on the second page.', 'bbox': [{'page_id': 1}]},
    ])
    write_document(jsons / 'doc_beta__1921.json', [
        {'id': 0, 'type': 'title', 'content': 'Combined Statement 1921'},
        {'id': 1, 'type': 'text', 'content': 'War expenditures nominal dollars 1921'},
    ])
    # 1-based raw page ids -> must become canonical 0-based page_index 0..1
    write_document(jsons / 'doc_gamma__1900.json', [
        {'id': 0, 'type': 'title', 'content': 'Receipts overview 1900', 'bbox': [{'page_id': 1}]},
        {'id': 1, 'type': 'text', 'content': 'Second raw page disbursements', 'bbox': [{'page_id': 2}]},
    ])
    for stem in ('doc_alpha__1854', 'doc_beta__1921', 'doc_gamma__1900'):
        (txts / f'{stem}.txt').write_text(f'{stem} flat text\n', encoding='utf-8')
    data_root = root / 'data'
    ca.stage_documents(source, data_root / 'corpus')
    manifest = ca.build_index(data_root / 'corpus')
    return data_root / 'corpus', manifest


def test_build_indexes_every_document_and_probe_is_green(corpus):
    root, manifest = corpus
    assert manifest['documents'] == 3
    assert manifest['total_elements'] == 8
    report = ca.probe(root, write_report=False)
    assert report['status'] == 'green'
    assert report['documents'] == report['findable'] == report['searchable'] == report['readable'] == 3
    # 探针不读题目：报告里没有 question/gold 概念，只有语料自身统计
    assert set(report) >= {'documents', 'generic_query_hits', 'failures'}


def test_element_per_line_and_page_normalization(corpus):
    root, _ = corpus
    with ca.CorpusAdapter(root) as adapter:
        alpha = adapter.read('doc_alpha__1854', 1, 4)
        assert [line['line'] for line in alpha['lines']] == [1, 2, 3, 4]
        assert alpha['lines'][3]['page_id'] == 1 and alpha['lines'][3]['raw_page_id'] == 1
        gamma_page0 = adapter.page('doc_gamma__1900', 0)
        assert gamma_page0['pages'] == 2  # 两个 raw 页（1、2）规范为 0、1
        assert gamma_page0['elements'][0]['raw_page_id'] == 1
        with pytest.raises(ca.CorpusError, match='no page_index=2'):
            adapter.page('doc_gamma__1900', 2)
        docs = {row['name']: row for row in adapter.docs()}
        assert docs['doc_alpha__1854']['elements'] == 4


def test_search_read_page_agree_with_evidence_locators(corpus):
    root, _ = corpus
    with ca.CorpusAdapter(root) as adapter:
        hits = adapter.search('customs collections')
        assert [(hit['name'], hit['line']) for hit in hits] == [('doc_alpha__1854', 2)]
        assert '«Customs»' in hits[0]['snippet']
        hit = adapter.search('disbursements', doc='doc_gamma__1900')[0]
        snippet = adapter.read('doc_gamma__1900', hit['line'], hit['line'])
        assert snippet['lines'][0]['page_id'] == hit['page_id'] == 1
        assert 'disbursements' in snippet['lines'][0]['content']


def test_fts_syntax_injection_is_neutralized(corpus):
    root, _ = corpus
    with ca.CorpusAdapter(root) as adapter:
        # 任意文本被拆成 AND 连接的引号词组：运算符/括号/引号都不再生效
        assert ca._fts_query(['"customs"', 'OR', '(delete)']) == '"customs" AND "OR" AND "delete"'
        assert adapter.search('customs OR (bulletins)') == []  # AND 语义，不会放宽成 OR
        assert adapter.search('customs (reached)')[0]['name'] == 'doc_alpha__1854'
        with pytest.raises(ca.CorpusError):
            adapter.search('!!!')


def test_accept_registers_a_real_product_source(corpus):
    from briefloop.store import Store
    root, _ = corpus
    store = Store(corpus[0].parent / 'workspace')
    run = store.create_run({'title': 'QA smoke', 'objective': 'q', 'allow_web': True}, [])
    with ca.CorpusAdapter(root) as adapter:
        accepted = adapter.accept(store, run['id'], 'doc_alpha__1854')
        assert accepted['lines'] == 4 and accepted['pages'] == 2
        assert accepted['source_id'] in store.source_ids(run['id'])
        assert store.source_text(accepted['source_id']) == adapter.document_text('doc_alpha__1854')
        # 证据附件的 line_range 定位能按同一投影核对（validate_evidence 的诊断路径）
        lines = store.source_text(accepted['source_id']).splitlines()
        assert lines[1] == 'Customs collections reached 1,234 dollars in 1854.'


def test_staging_fails_closed_on_mismatched_corpus_sides(tmp_path):
    source = tmp_path / 'fullcorpus'
    (source / 'parsed_corpus' / 'jsons').mkdir(parents=True)
    (source / 'treasury_bulletins_parsed' / 'transformed').mkdir(parents=True)
    write_document(source / 'parsed_corpus' / 'jsons' / 'only_json.json',
                   [{'id': 0, 'type': 'text', 'content': 'lonely'}])
    with pytest.raises(ca.CorpusError, match='disagree'):
        ca.stage_documents(source, tmp_path / 'corpus')


def test_probe_turns_red_when_a_staged_document_disappears(corpus):
    root, _ = corpus
    hidden = root / 'documents' / 'parsed' / 'doc_beta__1921.json'
    saved = hidden.read_bytes()
    hidden.unlink()
    try:
        report = ca.probe(root, write_report=False)
        assert report['status'] == 'red'
        assert any(failure['doc'] == 'doc_beta__1921' and failure['check'] == 'findable'
                   for failure in report['failures'])
    finally:
        hidden.write_bytes(saved)


def test_adapter_is_query_blind_by_construction():
    """模块级静态断言：适配器不引用题面/gold/HF 凭据的任何文件名或路径。"""
    source = (EXPERIMENT / 'corpus_adapter.py').read_text(encoding='utf-8')
    for forbidden in ('question_only', 'evaluator-only', 'officeqa_pro', '.csv', 'HF_TOKEN',
                      'source_files', 'source_docs'):
        assert forbidden not in source, forbidden


def test_shared_adapter_is_thread_safe_for_episode_workers(corpus):
    root, _ = corpus
    with ca.CorpusAdapter(root) as adapter:
        results = []

        def search():
            results.append(len(adapter.search('customs')))

        threads = [threading.Thread(target=search) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results == [1, 1, 1, 1]
