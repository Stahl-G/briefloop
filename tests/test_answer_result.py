"""grounded_qa_v1 输出模式（设计 §3.Q1 / 协议 BL-OQA-SR-v1.0 §5.3）行为测试。

零真实模型调用：全部用 FakeRuntime 驱动。覆盖——
* Requirements.result_format 显式 opt-in，未知值拒绝，默认报告路径不变；
* 机械投影与 Q0 官方适配器逐例等价（同一裁决，不另建评分器）；
* QA 保存：answer.json(+可选附件) → 版本结构化内容 + 程序生成的最短投影；
* 裸 URL / 范围外 source_id / 不可解析 locator 的附件被拒绝；
* 修订必须新答案版本，审阅指纹绑定答案与附件哈希，旧核查不继承；
* 会话在接纳后失败，已接纳答案仍可读。
"""
import json
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments' / 'officeqa_structured'
sys.path.insert(0, str(EXPERIMENT))

import score_answer_record as sar  # noqa: E402
from briefloop.answer_result import (ANSWER_SCHEMA_JSON, EVIDENCE_SCHEMA_JSON, RESULT_FORMAT,  # noqa: E402
                                     answer_of, build_answer_draft, project_answer, qa_projection,
                                     validate_evidence)
from briefloop.models import Requirements  # noqa: E402
from briefloop.review import _snapshot, build_packet, run_review, validate_applicable_review  # noqa: E402
from briefloop.runtime import Worker  # noqa: E402
from briefloop.store import Store  # noqa: E402
from pydantic import ValidationError  # noqa: E402


def answer_payload(answer=..., status='answered', **extra):
    record = {'schema_version': 'officeqa.answer.v1', 'status': status}
    record['answer'] = None if answer is ... else answer
    record.update(extra)
    return json.dumps(record, ensure_ascii=False)


def evidence_payload(source_id, locator, excerpt='Revenue 12 million USD.'):
    return json.dumps({'schema_version': 'officeqa.evidence.v1',
                       'evidence': [{'source_id': source_id, 'locator': locator, 'excerpt': excerpt}],
                       'calculations': [], 'limitations': []}, ensure_ascii=False)


def qa_run(tmp_path, *, requirements=None):
    store = Store(tmp_path)
    src = store.add_source('Source', 'Revenue 12 million USD.\nGrowth 12.5 percent.')
    req = {'title': 'Revenue question', 'objective': 'What is the revenue?', 'result_format': RESULT_FORMAT}
    req.update(requirements or {})
    run = store.create_run(req, [src['id']])
    return store, src, run


class FakeRuntime:
    """Writes program-checkable artifacts; never a real model call."""

    def __init__(self, answer='12 million', evidence=None, fail_after_publish=False):
        self.cancelled = threading.Event()
        self.answer = answer
        self.evidence = evidence
        self.fail_after_publish = fail_after_publish
        self.prompts = []

    def execute(self, job, prompt, folder, on_tick=lambda: None, **kwargs):
        self.prompts.append(prompt)
        target = Path(folder)
        if '独立只读 Reviewer' in prompt:
            match = re.search(r'version_id=(brief_\w+?)，fingerprint=([0-9a-f]{64})', prompt)
            target.joinpath('review.json').write_text(json.dumps({
                'fingerprint': match.group(2), 'version_id': match.group(1), 'status': 'complete',
                'summary': 'Checked the frozen answer projection', 'coverage_scan_complete': True,
                'assessment': {'brief_hash': re.search(r'assessment\.brief_hash=([0-9a-f]{64})', prompt).group(1),
                               'status': 'complete', 'summary': 'QA checked', 'overall': '达到要求',
                               'evidence': 4, 'coverage': 4, 'analysis': 4, 'expression': 4},
                'findings': []}, ensure_ascii=False), encoding='utf-8')
            return {}
        if target.name == 'revision':
            target.joinpath('answer.json').write_text(answer_payload(self.answer), encoding='utf-8')
            target.joinpath('responses.json').write_text('[]', encoding='utf-8')
            return {}
        target.joinpath('answer.json').write_text(answer_payload(self.answer), encoding='utf-8')
        if self.evidence is not None:
            target.joinpath('evidence_draft.json').write_text(self.evidence, encoding='utf-8')
        on_tick()
        if self.fail_after_publish:
            raise RuntimeError('transport died after admission')
        return {}


class ReportFakeRuntime:
    """The default report path still hands in draft.json."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.prompts = []

    def execute(self, job, prompt, folder, on_tick=lambda: None, **kwargs):
        self.prompts.append(prompt)
        Path(folder, 'draft.json').write_text(json.dumps({'title': 'Report',
                                                          'markdown': 'Revenue 12 million USD.'}), encoding='utf-8')
        on_tick()
        return {}


# --- opt-in contract ---------------------------------------------------------

def test_result_format_opt_in_and_unknown_values_rejected():
    assert Requirements.model_validate({'title': 't', 'objective': 'o'}).result_format is None
    assert Requirements.model_validate({'title': 't', 'objective': 'o',
                                        'result_format': RESULT_FORMAT}).result_format == RESULT_FORMAT
    with pytest.raises(ValidationError):
        Requirements.model_validate({'title': 't', 'objective': 'o', 'result_format': 'grounded_qa_v2'})
    with pytest.raises(ValidationError):
        Requirements.model_validate({'title': 't', 'objective': 'o', 'result_format': ''})


def test_default_run_keeps_report_path_unchanged(tmp_path):
    store, src, run = qa_run(tmp_path, requirements={'result_format': None})
    assert json.loads(run['requirements'])['result_format'] is None
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': 'Revenue 12 million USD.'})
    detail = json.loads(brief['detail'])
    assert detail['answer_result'] is None and detail['answer_evidence'] is None
    assert 'grounded_qa' not in _snapshot(store, brief['id'])
    worker = Worker(store)
    worker.runtime = ReportFakeRuntime()
    job = store.enqueue('generate', {'run_id': run['id']})
    result = worker.generate(job, score=False)
    saved = store.one('briefs', result['version_id'])
    assert saved['markdown'] == 'Revenue 12 million USD.'
    assert json.loads(saved['detail'])['answer_result'] is None
    assert answer_of(store, saved['id']) is None
    assert '正文目标约' in worker.runtime.prompts[0]  # report length method intact on the default path


# --- mechanical projection stays the Q0 gold-blind rules ---------------------

def test_projection_is_verdict_identical_to_the_q0_adapter():
    payloads = [
        answer_payload('543'),
        answer_payload('  543  '),
        answer_payload('12.50'),
        answer_payload('[North, 0.866]'),
        answer_payload(None, status='abstained'),
        answer_payload(''),
        answer_payload('   '),
        answer_payload('543\n544'),
        answer_payload('5' * 251),
        answer_payload('999<FINAL_ANSWER>543</FINAL_ANSWER>'),
        answer_payload('<final_answer>543</final_answer>'),
        answer_payload('FINAL_ANSWER: 543'),
        answer_payload(12.5),
        answer_payload(['8', '152260']),
        answer_payload(True),
        answer_payload('543', schema_version='other.v1'),
        answer_payload('543', status='ready'),
        answer_payload('543', status='abstained'),
        answer_payload('543', score=1.0, ready=True),
        '{"schema_version": "officeqa.answer.v1", "status": "answered", "answer": "543", "answer": "544"}',
        answer_payload('543') + '\n' + answer_payload('544'),
        '[' + answer_payload('543') + ']',
        '```json\n' + answer_payload('543') + '\n```',
        '{"schema_version": "officeqa.answer.v1", "status": "answered", "answer": NaN}',
        '{"schema_version": "officeqa.answer.v1", "status": "answered"}',
        b'{"schema_version": "officeqa.answer.v1", "status": "answered", "answer": "5\xfftens"}',
    ]
    for raw in payloads:
        mine = project_answer(raw)
        theirs = sar.project_answer(raw)
        assert (mine.status, mine.answer, mine.reason, mine.warnings) == \
               (theirs.status, theirs.answer, theirs.reason, theirs.warnings), raw
        if mine.status == 'answered':
            assert mine.submission_text == theirs.submission_text


def test_agent_facing_schemas_pin_the_same_constraints_as_the_experiment():
    answer = json.loads(ANSWER_SCHEMA_JSON)
    assert answer['$id'] == 'officeqa.answer.v1'
    assert answer['required'] == ['schema_version', 'status', 'answer']
    assert answer['properties']['schema_version']['const'] == 'officeqa.answer.v1'
    assert answer['properties']['status']['enum'] == ['answered', 'abstained']
    string = next(v for v in answer['properties']['answer']['anyOf'] if v.get('type') == 'string')
    assert string['maxLength'] == 250 and '\\n' in string['pattern']
    evidence = json.loads(EVIDENCE_SCHEMA_JSON)
    assert evidence['$id'] == 'officeqa.evidence.v1'
    assert evidence['required'] == ['schema_version', 'evidence', 'calculations', 'limitations']
    line_range, page = evidence['properties']['evidence']['items']['properties']['locator']['oneOf']
    assert line_range['properties']['kind']['const'] == 'line_range'
    assert line_range['properties']['start']['minimum'] == 1  # 1-based lines
    assert page['properties']['page_index']['minimum'] == 0  # 0-based page index


# --- QA save: answer becomes the version's structured content ---------------

def test_qa_generate_saves_answer_and_mechanical_projection(tmp_path):
    store, src, run = qa_run(tmp_path)
    evidence = evidence_payload(src['id'], {'kind': 'line_range', 'start': 1, 'end': 1})
    worker = Worker(store)
    worker.runtime = FakeRuntime(answer='12 million', evidence=evidence)
    job = store.enqueue('generate', {'run_id': run['id']})
    result = worker.generate(job, score=False)
    brief = store.one('briefs', result['version_id'])
    detail = json.loads(brief['detail'])
    assert detail['answer_result'] == {'schema_version': 'officeqa.answer.v1',
                                       'status': 'answered', 'answer': '12 million'}
    assert detail['answer_evidence']['evidence'][0]['source_id'] == src['id']
    assert brief['editor_document'] is None  # markdown projection only
    assert brief['markdown'] == qa_projection(json.loads(run['requirements']),
                                              detail['answer_result'], detail['answer_evidence'])
    assert '答案：12 million' in brief['markdown'] and src['id'] in brief['markdown']
    accepted = answer_of(store, brief['id'])
    assert accepted['answer']['answer'] == '12 million' and accepted['answer_sha256']
    assert src['id'] in store.source_ids(run['id'])  # citation registered onto the run
    snapshot = _snapshot(store, brief['id'])
    assert snapshot['grounded_qa']['answer_sha256'] == accepted['answer_sha256']
    assert snapshot['grounded_qa']['evidence_sha256'] == accepted['evidence_sha256']


def test_qa_generate_prompt_is_answer_method_not_report_method(tmp_path):
    store, src, run = qa_run(tmp_path)
    worker = Worker(store)
    worker.runtime = FakeRuntime()
    job = store.enqueue('generate', {'run_id': run['id']})
    folder = store.root / 'jobs' / job['id']
    worker.generate(job, score=False)
    prompt = worker.runtime.prompts[0]
    assert '回答该问题、识别证据不足或冲突、核对单位和计算' in prompt
    assert 'answer.json' in prompt and 'check-answer' in prompt
    assert '正文目标约' not in prompt
    assert 'set_reader_contract' not in prompt and 'reader_contract.schema.json' not in prompt
    assert (folder / 'answer.schema.json').exists() and (folder / 'evidence.schema.json').exists()
    assert '问答' in (folder / 'scout-contract.md').read_text(encoding='utf-8')


def test_abstained_answer_saved_as_projection(tmp_path):
    store, src, run = qa_run(tmp_path)

    class AbstainRuntime(FakeRuntime):
        def execute(self, job, prompt, folder, on_tick=lambda: None, **kwargs):
            Path(folder, 'answer.json').write_text(answer_payload(None, status='abstained'), encoding='utf-8')
            on_tick()
            return {}
    worker = Worker(store)
    worker.runtime = AbstainRuntime()
    job = store.enqueue('generate', {'run_id': run['id']})
    result = worker.generate(job, score=False)
    brief = store.one('briefs', result['version_id'])
    assert '答案：（abstained，未作答）' in brief['markdown']
    assert answer_of(store, brief['id'])['answer']['status'] == 'abstained'


def test_invalid_answer_fails_admission_and_original_is_kept(tmp_path):
    store, src, run = qa_run(tmp_path)
    worker = Worker(store)
    worker.runtime = FakeRuntime(answer='543\n544')  # multiline violates the contract
    job = store.enqueue('generate', {'run_id': run['id']})
    with pytest.raises(ValueError, match='single line|单行'):
        worker.generate(job, score=False)
    assert store.rows('SELECT id FROM briefs WHERE run_id=?', (run['id'],)) == []
    preserved = json.loads((store.root / 'jobs' / job['id'] / 'answer-invalid.json').read_text(encoding='utf-8'))
    assert preserved['answer'] == '543\n544'


# --- evidence attachment gates: run-scoped ids, parseable locators ----------

def test_bare_url_and_foreign_source_ids_rejected(tmp_path):
    store, src, run = qa_run(tmp_path)
    for bad in ['https://example.com/report.pdf', 'www.example.com/report.pdf', 'src_not_registered']:
        report = validate_evidence(store, run['id'],
                                   evidence_payload(bad, {'kind': 'line_range', 'start': 1, 'end': 1}))
        assert report['status'] == 'invalid', bad
        assert {error['code'] for error in report['errors']} & {'bare_url', 'source_not_in_run'}, bad
    worker = Worker(store)
    worker.runtime = FakeRuntime(answer='12 million',
                                 evidence=evidence_payload('https://example.com/report.pdf',
                                                           {'kind': 'page', 'page_index': 2}))
    job = store.enqueue('generate', {'run_id': run['id']})
    with pytest.raises(ValueError, match='bare_url|裸 URL'):
        worker.generate(job, score=False)
    assert store.rows('SELECT id FROM briefs WHERE run_id=?', (run['id'],)) == []


def test_unparseable_locators_and_calculation_references_rejected(tmp_path):
    store, src, run = qa_run(tmp_path)
    for locator in [{'kind': 'line_range', 'start': 3, 'end': 1},
                    {'kind': 'line_range', 'start': 0, 'end': 2},
                    {'kind': 'page', 'page_index': -1},
                    {'kind': 'page'},
                    {'kind': 'chapter', 'number': 1}]:
        report = validate_evidence(store, run['id'], evidence_payload(src['id'], locator))
        assert report['status'] == 'invalid' and any(e['code'] == 'locator_invalid' for e in report['errors'])
    raw = json.dumps({'schema_version': 'officeqa.evidence.v1',
                      'evidence': [{'source_id': src['id'], 'locator': {'kind': 'line_range', 'start': 1, 'end': 1},
                                    'excerpt': 'Revenue 12 million USD.'}],
                      'calculations': [{'expression': '12*1.045', 'inputs': [{'name': 'base', 'value': '12',
                                                                              'evidence_index': 7}], 'result': '12.54'}],
                      'limitations': []})
    report = validate_evidence(store, run['id'], raw)
    assert any(e['code'] == 'evidence_index_range' for e in report['errors'])
    # excerpt location is recorded as a diagnostic, never an admission gate
    good = validate_evidence(store, run['id'], evidence_payload(src['id'], {'kind': 'line_range', 'start': 2, 'end': 2},
                                                                excerpt='Growth 12.5 percent.'))
    assert good['status'] == 'ok' and good['entries'][0]['excerpt_located'] is True
    miss = validate_evidence(store, run['id'], evidence_payload(src['id'], {'kind': 'line_range', 'start': 1, 'end': 1},
                                                                excerpt='Not in those lines'))
    assert miss['status'] == 'ok' and miss['entries'][0]['excerpt_located'] is False


def test_check_answer_tool_reports_bare_url(tmp_path, monkeypatch, capsys):
    store, src, run = qa_run(tmp_path)
    answer_file = tmp_path / 'answer.json'
    answer_file.write_text(answer_payload('12 million'), encoding='utf-8')
    evidence_file = tmp_path / 'evidence_draft.json'
    evidence_file.write_text(evidence_payload('https://example.com/report.pdf', {'kind': 'page', 'page_index': 0}),
                             encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['briefloop', 'tool', '--workspace', str(store.root), 'check-answer',
                                     '--file', str(answer_file), '--evidence', str(evidence_file),
                                     '--run', run['id']])
    from briefloop.cli import main
    with pytest.raises(SystemExit) as info:
        main()
    assert info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'invalid'
    assert 'bare_url' in {error['code'] for error in payload['errors']}
    monkeypatch.setattr('sys.argv', ['briefloop', 'tool', '--workspace', str(store.root), 'check-answer',
                                     '--file', str(answer_file), '--run', run['id']])
    main()
    assert json.loads(capsys.readouterr().out)['status'] == 'ok'


# --- no LLM-written brief may pose as a QA version ---------------------------

def test_hand_written_brief_cannot_replace_the_projection(tmp_path):
    store, src, run = qa_run(tmp_path)
    folder = tmp_path / 'submit'
    folder.mkdir()
    (folder / 'answer.json').write_text(answer_payload('12 million'), encoding='utf-8')
    data = build_answer_draft(store, run, folder)
    with pytest.raises(ValueError, match='机械投影'):
        store.publish(run['id'], {**data, 'markdown': '# 我写的报告\n\n正文很长。'})
    # answer fields are QA-only: a normal report cannot carry them either
    plain = Store(tmp_path / 'plain')
    plain_src = plain.add_source('S', 'text')
    plain_run = plain.create_run({'title': 'R', 'objective': 'O'}, [plain_src['id']])
    with pytest.raises(ValueError, match='grounded_qa_v1'):
        plain.publish(plain_run['id'], {'title': 'R', 'markdown': 'Body.',
                                        'answer_result': {'schema_version': 'officeqa.answer.v1',
                                                          'status': 'answered', 'answer': '12 million'}})


# --- revision binding: new answer version, old review not inherited ---------

def test_qa_revision_requires_new_answer_and_old_review_stays_bound(tmp_path):
    store, src, run = qa_run(tmp_path)
    worker = Worker(store)
    worker.runtime = FakeRuntime(answer='12 million')
    job = store.enqueue('generate', {'run_id': run['id'], 'auto_revision': True})
    result = worker.generate(job, score=False)
    v1 = store.one('briefs', result['version_id'])
    review_job = store.enqueue('review', {'version_id': v1['id'], 'parent_job_id': job['id']})
    review = run_review(store, FakeRuntime(), store.one('jobs', review_job['id']), v1['id'],
                        store.root / 'jobs' / review_job['id'])
    assert review['status'] == 'complete'
    fp1 = _snapshot(store, v1['id'])['grounded_qa']['answer_sha256']

    store.assess(v1['id'], {'brief_hash': v1['hash'], 'status': 'complete', 'summary': 'unit mismatch',
                            'overall': '建议修改', 'evidence': 2, 'coverage': 3, 'analysis': 2, 'expression': 4,
                            'findings': [{'dimension': 'analysis', 'severity': 'major',
                                          'description': 'unit wrong', 'report_quote': '12 million'}]})
    worker.runtime = FakeRuntime(answer='12 million USD')
    outcome = worker.auto_revise(job, v1, worker.folder(store.one('jobs', job['id'])))
    v2 = store.one('briefs', outcome['version_id'])
    assert v2['id'] != v1['id'] and v2['parent_id'] == v1['id']
    assert answer_of(store, v2['id'])['answer']['answer'] == '12 million USD'
    assert '答案：12 million USD' in v2['markdown']
    assert _snapshot(store, v2['id'])['grounded_qa']['answer_sha256'] != fp1
    # the admitted review of v1 cannot be inherited by the new answer version
    with pytest.raises(ValueError, match='未绑定'):
        validate_applicable_review(store, review['id'], v2['id'])
    validate_applicable_review(store, review['id'], v1['id'])  # still valid for its own version
    assert build_packet(store, v1['id'], tmp_path / 'p1')[0] != build_packet(store, v2['id'], tmp_path / 'p2')[0]


def test_qa_revision_without_new_answer_fails(tmp_path):
    store, src, run = qa_run(tmp_path)
    worker = Worker(store)
    worker.runtime = FakeRuntime(answer='12 million')
    job = store.enqueue('generate', {'run_id': run['id']})
    result = worker.generate(job, score=False)
    v1 = store.one('briefs', result['version_id'])
    store.assess(v1['id'], {'brief_hash': v1['hash'], 'status': 'complete', 'summary': 'gap',
                            'overall': '存在重大问题', 'evidence': 2, 'coverage': 2, 'analysis': 2, 'expression': 3,
                            'findings': [{'dimension': 'coverage', 'severity': 'major',
                                          'description': 'missing requirement', 'report_quote': '12 million'}]})

    class NoAnswerRevision(FakeRuntime):
        def execute(self, job, prompt, folder, on_tick=lambda: None, **kwargs):
            self.prompts.append(prompt)  # revision turn ends without submitting answer.json
            return {}
    worker.runtime = NoAnswerRevision()
    with pytest.raises(ValueError, match='新的 answer.json'):
        worker.auto_revise(job, v1, worker.folder(store.one('jobs', job['id'])))
    assert store.rows('SELECT id FROM briefs WHERE parent_id=?', (v1['id'],)) == []


# --- accepted answers survive a later failure -------------------------------

def test_answer_readable_after_transport_dies_post_admission(tmp_path):
    store, src, run = qa_run(tmp_path)
    worker = Worker(store)
    worker.runtime = FakeRuntime(answer='12 million', fail_after_publish=True)
    job = store.enqueue('generate', {'run_id': run['id']})
    with pytest.raises(RuntimeError, match='transport died'):
        worker.generate(job, score=False)
    vid = 'brief_' + job['id'][4:]
    brief = store.one('briefs', vid)
    accepted = answer_of(store, vid)
    assert accepted['answer']['answer'] == '12 million'
    assert brief['markdown'] == qa_projection(json.loads(run['requirements']),
                                              json.loads(brief['detail'])['answer_result'],
                                              json.loads(brief['detail'])['answer_evidence'])


def test_review_of_tampered_projection_is_refused(tmp_path):
    store, src, run = qa_run(tmp_path)
    worker = Worker(store)
    worker.runtime = FakeRuntime(answer='12 million')
    job = store.enqueue('generate', {'run_id': run['id']})
    result = worker.generate(job, score=False)
    vid = result['version_id']
    with store.tx() as c:
        c.execute('UPDATE briefs SET markdown=? WHERE id=?', ('# 手改的正文\n', vid))
    with pytest.raises(ValueError, match='机械投影不一致'):
        _snapshot(store, vid)
