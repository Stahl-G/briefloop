"""corpus_adapter 行为测试（设计 §3.Q2 / 协议 §6.1）。

零模型调用、零题目数据：探针与检索只用语料自身的词。覆盖——
* build→probe 全绿；元素=行、page 规范化为 0-based（原文 1-based 也归一）；
* search/read/page 与证据 locator（1-based 行、0-based page_index）一致；
* FTS 注入被引号中和；纯标点查询关闭失败；
* accept 走产品真实接纳路径（Store.add_source + attach_source），正文与投影逐行对齐；
* query-blind：模块不存在读题面/gold 的代码路径；staged 文件缺失时探针变红；
* v1 纯文本语料：整文档+文件行号建模、page 语义 not_applicable、--format/--corpus CLI、
  接入 v1 时既有 V2 索引一个字节不动。
"""
import hashlib
import io
import json
import contextlib
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


# --- v1 纯文本语料（dev pilot 路由目标）-------------------------------------------

V1_TXT_1941 = (
    'TREASURY BULLETIN\n'
    'JANUARY 1941\n'
    '\n'
    'Expenditures for national defense  calendar year 1940\n'
    'Total  7,327   millions\n'
    '   aligned    table    row\n'
)
V1_TXT_1921 = 'Combined statement 1921\nWar expenditures nominal dollars\n'


@pytest.fixture(scope='module')
def v1_corpus(corpus):
    """在既有 V2 数据区旁接入 v1 纯文本语料；先快照 V2 索引字节用于“未变”断言。"""
    data_root = corpus[0].parent
    v2_manifest_bytes = (data_root / 'corpus' / 'index' / 'manifest.json').read_bytes()
    v2_db_sha = hashlib.sha256((data_root / 'corpus' / 'index' / 'corpus.sqlite').read_bytes()).hexdigest()
    source = data_root.parent / 'v1src'
    source.mkdir(parents=True, exist_ok=True)
    (source / 'treasury_bulletin_1941_01.txt').write_text(V1_TXT_1941, encoding='utf-8')
    (source / 'treasury_bulletin_1921_06.txt').write_text(V1_TXT_1921, encoding='utf-8')
    staging = ca.stage_documents(source, data_root / 'corpus-v1', fmt='v1')
    manifest = ca.build_index(data_root / 'corpus-v1', fmt='v1')
    return data_root, staging, manifest, (v2_manifest_bytes, v2_db_sha)


def test_v1_build_probe_green_and_v2_index_untouched(corpus, v1_corpus):
    data_root, staging, manifest, (v2_manifest_bytes, v2_db_sha) = v1_corpus
    assert staging['format'] == 'v1' and staging['documents'] == 2 and staging['pdf_documents'] == 0
    assert manifest['format'] == 'v1' and manifest['schema_version'] == 'officeqa.corpus_index_txt.v1'
    assert manifest['page_semantics'] == 'not_applicable'
    assert manifest['documents'] == 2
    assert manifest['total_elements'] == len(V1_TXT_1941.splitlines()) + len(V1_TXT_1921.splitlines())
    report = ca.probe(data_root / 'corpus-v1', write_report=False)
    assert report['status'] == 'green' and report['format'] == 'v1'
    assert report['page_semantics'] == 'not_applicable'
    assert report['documents'] == report['findable'] == report['readable'] == report['searchable'] == 2
    # 接入 v1 后，V2 主测试语料的索引一个字节未动（manifest 字节 + sqlite 哈希）
    v2_root = data_root / 'corpus'
    assert (v2_root / 'index' / 'manifest.json').read_bytes() == v2_manifest_bytes
    assert hashlib.sha256((v2_root / 'index' / 'corpus.sqlite').read_bytes()).hexdigest() == v2_db_sha
    # v1 索引里没有 JSON 解析侧：只 stage 了 documents/text
    assert (data_root / 'corpus-v1' / 'documents' / 'text').is_dir()
    assert not (data_root / 'corpus-v1' / 'documents' / 'parsed').exists()


def test_v1_whole_document_line_model_and_page_not_applicable(v1_corpus):
    data_root, _, manifest, _ = v1_corpus
    lines = V1_TXT_1941.split('\n')
    with ca.CorpusAdapter(data_root / 'corpus-v1') as adapter:
        assert adapter.format == 'v1'
        # 行号=文件物理行号（1-based），内容去掉行终止符后逐字保留（含表格对齐空白）
        read = adapter.read('treasury_bulletin_1941_01', 4, 6)
        assert [row['line'] for row in read['lines']] == [4, 5, 6]
        assert read['lines'][0]['content'] == lines[3]
        assert read['lines'][2]['content'] == '   aligned    table    row'
        assert all(row['page_id'] == 0 and row['raw_page_id'] == 0 and row['element_id'] is None
                   for row in read['lines'])
        # page 语义 not_applicable：拒绝而不是造页
        with pytest.raises(ca.CorpusError, match='not_applicable'):
            adapter.page('treasury_bulletin_1941_01', 0)
        # 检索命中带文件行号，与 read/证据 locator 一致
        hit = adapter.search('national defense', doc='treasury_bulletin_1941_01')[0]
        assert hit['line'] == 4 and '«national»' in hit['snippet']
        listing = adapter.docs()
        assert [row['name'] for row in listing] == ['treasury_bulletin_1921_06', 'treasury_bulletin_1941_01']
        assert all(row['pages'] == 0 for row in listing)  # not_applicable 如实记 0 页
        assert {row['name']: row['elements'] for row in listing}[
            'treasury_bulletin_1941_01'] == len(V1_TXT_1941.splitlines())
        # 整文档投影 = 文件内容本身（仅去掉最后行终止符）
        assert adapter.document_text('treasury_bulletin_1941_01') == V1_TXT_1941.removesuffix('\n')


def test_v1_accept_registers_a_real_product_source(v1_corpus):
    from briefloop.store import Store
    data_root, _, _, _ = v1_corpus
    store = Store(data_root / 'workspace-v1')
    run = store.create_run({'title': 'v1 smoke', 'objective': 'q', 'allow_web': True}, [])
    with ca.CorpusAdapter(data_root / 'corpus-v1') as adapter:
        accepted = adapter.accept(store, run['id'], 'treasury_bulletin_1921_06')
        assert accepted['lines'] == len(V1_TXT_1921.splitlines()) and accepted['pages'] == 0
        assert accepted['name'] == 'treasury_bulletin_1921_06.txt'
        assert store.source_text(accepted['source_id']) == adapter.document_text('treasury_bulletin_1921_06')
        assert accepted['source_id'] in store.source_ids(run['id'])


def test_v1_cli_format_and_corpus_flag_routing(tmp_path):
    source = tmp_path / 'v1'
    source.mkdir()
    (source / 'treasury_bulletin_1939_12.txt').write_text(
        'alpha treasury line one\nbeta receipts line two\n', encoding='utf-8')
    data_root = tmp_path / 'data'
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert ca.main(['build', '--format', 'v1', '--source', str(source),
                        '--data-root', str(data_root)]) == 0
    built = json.loads(buffer.getvalue())
    assert built['staging']['format'] == 'v1' and built['manifest']['format'] == 'v1'
    assert (data_root / 'corpus-v1' / 'index' / 'manifest.json').is_file()
    # probe/docs 按 --corpus 选目标；默认仍是 V2 corpus（未建 → 明确失败而非回落到 v1）
    with contextlib.redirect_stdout(io.StringIO()):
        assert ca.main(['probe', '--data-root', str(data_root), '--corpus', 'corpus-v1']) == 0
    assert ca.main(['probe', '--data-root', str(data_root)]) == 2
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert ca.main(['docs', '--data-root', str(data_root), '--corpus', 'corpus-v1']) == 0
    assert [row['name'] for row in json.loads(buffer.getvalue())] == ['treasury_bulletin_1939_12']
    # 未知格式失败关闭
    with pytest.raises(SystemExit):
        ca.main(['build', '--format', 'v9', '--source', str(source), '--data-root', str(data_root)])


def test_v1_staging_fails_closed_on_empty_source(tmp_path):
    source = tmp_path / 'empty'
    source.mkdir()
    with pytest.raises(ca.CorpusError, match='no \\*\\.txt'):
        ca.stage_documents(source, tmp_path / 'corpus-v1', fmt='v1')
    with pytest.raises(ca.CorpusError, match='unknown corpus format'):
        ca.stage_documents(source, tmp_path / 'x', fmt='v9')
