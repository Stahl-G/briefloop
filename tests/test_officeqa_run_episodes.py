"""run_episodes 行为测试（设计 §3.Q2 / 协议 §3.3、§8、§9）。

零真实模型调用：A/B 双组都走 stub，但 B 驱动真实 external_requests
submit/query + Worker generate→独立审阅→一次修订链与 grounded_qa_v1 保存路径。
覆盖——
* 合成数据全链：prepare→probe→run(--dry-run)→freeze→score；
* episode_record.json 按 §3.3 字段齐（提交序列、SHA-256、顺序号、预算、B 的
  run/job/version 绑定、usage_complete 如实为 false）；
* gold 不出现在 question_only/ 与 episodes/ 的任何文件；
* 无 HF_TOKEN 依赖；runner 除 --dry-run 外无真实执行路径（拒绝且零副作用）；
* 不引用原 datasets 目录；评分没有冻结则拒绝。
"""
import hashlib
import json
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments' / 'officeqa_structured'
sys.path.insert(0, str(EXPERIMENT))

import corpus_adapter as ca  # noqa: E402
import prepare_dataset as pd  # noqa: E402
import run_episodes as re_mod  # noqa: E402

GOLD_A = 'unobtainium-9931'
GOLD_B = '[vibranium-4477, 0.123]'
QUESTION_A = 'What was the total customs revenue collected in fiscal year 1854, in dollars?'
QUESTION_B = 'Which fiscal year shows war expenditures of exactly 1921 million dollars?'
# 历史 Pro v1 池（开发 pilot 的来源；题面与 v2 不同哈希）
HIST_QUESTION = 'What were total expenditures for national defense in calendar year 1940, in millions?'
HIST_GOLD = 'adamanthistory-7700'


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


@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    """prepare→probe→dry-run→freeze→score 的完整合成链（两个 case × A/B）。"""
    base = tmp_path_factory.mktemp('chain')
    source = base / 'datasetsrc'
    (source / 'fullcorpus' / 'parsed_corpus' / 'jsons').mkdir(parents=True)
    (source / 'fullcorpus' / 'treasury_bulletins_parsed' / 'transformed').mkdir(parents=True)
    write_document(source / 'fullcorpus' / 'parsed_corpus' / 'jsons' / 'doc_alpha__1854.json', [
        {'id': 0, 'type': 'page_header', 'content': 'TREASURY BULLETIN'},
        {'id': 1, 'type': 'text', 'content': 'Customs revenue collected 1,234 dollars in 1854.'},
        {'id': 2, 'type': 'table', 'content': '<table><tr><td>Year</td><td>1854</td></tr></table>'},
    ])
    write_document(source / 'fullcorpus' / 'parsed_corpus' / 'jsons' / 'doc_beta__1921.json', [
        {'id': 0, 'type': 'title', 'content': 'Combined Statement 1921'},
        {'id': 1, 'type': 'text', 'content': 'War expenditures nominal dollars 1921'},
    ])
    for stem in ('doc_alpha__1854', 'doc_beta__1921'):
        (source / 'fullcorpus' / 'treasury_bulletins_parsed' / 'transformed' / f'{stem}.txt').write_text(
            f'{stem} flat text\n', encoding='utf-8')
    fields = ['uid', 'question', 'answer', 'source_docs', 'source_files']
    write_csv(source / 'officeqa_pro_v2.csv', [
        {'uid': '0', 'question': QUESTION_A, 'answer': GOLD_A, 'source_docs': '', 'source_files': 'doc_alpha__1854.txt'},
        {'uid': '1', 'question': QUESTION_B, 'answer': GOLD_B, 'source_docs': '', 'source_files': 'doc_beta__1921.txt'},
    ], fields)
    write_csv(source / 'officeqa_pro_v2.normalized.csv', [
        {'uid': '1', 'question': QUESTION_B, 'answer': GOLD_B, 'source_docs': '', 'source_files': 'doc_beta__1921.txt'},
        {'uid': '0', 'question': QUESTION_A, 'answer': GOLD_A, 'source_docs': '', 'source_files': 'doc_alpha__1854.txt'},
    ], fields)
    write_csv(source / 'officeqa_pro.csv', [
        {'uid': 'UID0031', 'question': HIST_QUESTION, 'answer': HIST_GOLD, 'source_docs': '',
         'source_files': 'treasury_bulletin_1941_01.txt'},
    ], fields)
    data_root = base / 'data'
    pd.prepare(source, data_root)
    assert ca.probe(data_root / 'corpus', write_report=False)['status'] == 'green'
    assert re_mod.main(['run', '--data-root', str(data_root), '--run-label', 'chain',
                        '--cases', '2', '--dry-run', '--seed', '3']) == 0
    assert re_mod.main(['freeze', '--data-root', str(data_root), '--run-label', 'chain']) == 0
    assert re_mod.main(['score', '--data-root', str(data_root), '--run-label', 'chain']) == 0
    return data_root


def test_episode_records_carry_protocol_33_fields(chain):
    index = json.loads((chain / 'episodes' / 'chain' / 'run_index.json').read_text(encoding='utf-8'))
    assert index['dry_run'] is True and index['seed'] == 3
    assert len(index['episodes']) == 4 and not index['failures']
    orders = {(episode['case_key'], episode['arm']): episode['arm_order_index'] for episode in index['episodes']}
    for case in index['cases']:
        assert sorted(orders[(case['case_key'], arm)] for arm in ('A', 'B')) == [1, 2]  # 随机 A/B 先后
    for episode in index['episodes']:
        for field in ('protocol_id', 'experiment_id', 'dataset', 'case_key', 'uid', 'question_sha256',
                      'arm', 'arm_order_index', 'identifiers', 'started_at', 'deadline_at', 'finished_at',
                      'budget', 'tool_calls', 'usage_complete', 'submissions', 'chosen_submission',
                      'workspace', 'error'):
            assert field in episode, field
        assert episode['protocol_id'] == 'BL-OQA-SR-v1.0'
        config = re_mod.load_config()
        budget = re_mod.Budget.from_config(config)
        assert episode['budget'] == {'wall_clock_seconds': budget.wall_clock_seconds,
                                     'max_concurrent_model_calls': budget.max_concurrent_model_calls,
                                     'max_automatic_revisions_B': budget.max_automatic_revisions_B,
                                     'format_repair_attempts': budget.format_repair_attempts,
                                     'search_budget': dict(budget.search_budget),
                                     'input_tokens': None, 'output_reasoning_tokens': None,
                                     'tool_calls': None}
        assert episode['usage_complete'] is False  # stub：用量不完整要如实标记
        assert episode['format_repairs_issued'] >= 0  # §4 修复次数入档
        for submission in episode['submissions']:
            if not submission.get('accepted'):
                continue  # 失败尝试与 format-repair 记录不占顺序号
            payload = (Path(episode['workspace']) / 'submissions' /
                       f"{submission['seq']:04d}-answer.json").read_bytes()
            assert hashlib.sha256(payload).hexdigest() == submission['sha256']


def test_b_drives_real_run_review_revision_and_freezes_the_latest(chain):
    index = json.loads((chain / 'episodes' / 'chain' / 'run_index.json').read_text(encoding='utf-8'))
    for episode in index['episodes']:
        if episode['arm'] != 'B':
            continue
        linkage = episode['briefloop']
        assert linkage['job_status'] == 'complete'
        assert linkage['run_id'].startswith('run_') and linkage['job_id'].startswith('job_')
        # 初始版本 + 恰好一次自动修订；审阅→修订→再审阅
        assert [role['role'] for role in linkage['model_calls']] == ['orchestrator', 'reviewer', 'revision', 'reviewer']
        assert len(linkage['version_ids']) == 2
        assert episode['chosen_submission']['seq'] == 2  # 截止前最新已接纳答案
        answer = json.loads((Path(episode['workspace']) / 'submissions' / '0002-answer.json').read_bytes())
        assert answer['answer'] == re_mod.StubBriefloopTransport.revised_answer
        # 语料工具被真实使用并接纳来源
        assert {'search', 'accept', 'read', 'check-answer'} <= {call['op'] for call in episode['tool_calls']}


def test_shared_corpus_surface_is_identical_for_both_arms(chain):
    index = json.loads((chain / 'episodes' / 'chain' / 'run_index.json').read_text(encoding='utf-8'))
    by_arm = {arm: [e for e in index['episodes'] if e['arm'] == arm][0] for arm in ('A', 'B')}
    a_prompt = (Path(by_arm['A']['workspace']) / 'workspace' / 'prompt.md').read_text(encoding='utf-8')
    instructions = re_mod.corpus_tool_instructions(chain)
    assert instructions in a_prompt
    # 无受控联网入口接入时，指令必须明说没有，而不是宣称有一个不存在的入口
    assert '联网搜索入口可用于补充公开资料' not in instructions
    assert '未接入联网搜索入口' in instructions
    assert index['web_entry'] is None
    import sqlite3
    connection = sqlite3.connect(Path(by_arm['B']['workspace']) / 'workspace' / 'briefloop.db')
    requirements = json.loads(connection.execute('SELECT requirements FROM runs').fetchone()[0])
    settings = json.loads(connection.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0])
    connection.close()
    assert requirements['raw_input'] == instructions  # B 与 A 看到同一语料面
    assert requirements['allow_web'] is False  # 声明与能力一致
    # 设计 §4 关闭清单在工作区设置层落实（多搜索渠道/企业背景/研究档位显式固定）
    assert settings['search_policy']['primary_provider'] == 'native'
    assert settings['search_policy']['native_search_enabled'] is False
    assert settings['company_context_enabled'] is False
    assert settings['research_tier'] == 'standard'
    assert settings['max_reports'] == 1  # 不是 §8.1 的模型调用上限，语义分离
    assert requirements['result_format'] == 'grounded_qa_v1'
    for forbidden in (GOLD_A, GOLD_B, 'doc_alpha__1854.txt', 'doc_beta__1921.txt'):
        assert forbidden not in a_prompt


def test_arm_a_submits_through_the_named_submit_directory_with_format_repair(chain):
    """§8.3 提交端点 + §4 格式修复：A 桩先交违规载荷，收到 gold-blind 反馈后自行重交。"""
    index = json.loads((chain / 'episodes' / 'chain' / 'run_index.json').read_text(encoding='utf-8'))
    for episode in [e for e in index['episodes'] if e['arm'] == 'A']:
        prompt = (Path(episode['workspace']) / 'workspace' / 'prompt.md').read_text(encoding='utf-8')
        submit_dir = Path(episode['workspace']) / 'submit'
        assert submit_dir.is_dir() and str(submit_dir) in prompt  # 端点被创建并写进指令
        rows = episode['submissions']
        invalid = [row for row in rows if row['source'] == 'submit-dir:answer.json'
                   and row['status'] == 'invalid']
        repair = [row for row in rows if row['source'] == 'format-repair']
        accepted = [row for row in rows if row.get('accepted')]
        assert len(invalid) == 1 and invalid[0]['accepted'] is False  # 第一次尝试确实违规
        assert episode['format_repairs_issued'] == 1 and len(repair) == 1  # 恰好一次修复（整集共用）
        assert repair[0]['for_sha256'] == invalid[0]['sha256']  # 修复绑定到那次违规提交
        assert len(accepted) == 1 and accepted[0]['seq'] == 1
        assert episode['chosen_submission']['seq'] == 1  # 修复后的重交是被评分答案
        assert any(call['op'] == 'format-repair-resubmit' for call in episode['tool_calls'])
        # 修复反馈由程序生成但不代答：attempts 里保留原始违规字节
        attempt = (Path(episode['workspace']) / 'attempts' / '0001-answer.json').read_bytes()
        assert b'FINAL_ANSWER' in attempt


def test_run_index_records_concurrency_and_isolation(chain):
    index = json.loads((chain / 'episodes' / 'chain' / 'run_index.json').read_text(encoding='utf-8'))
    # §8.1：并发上限是“同时活跃模型调用”，由 run 级 ModelCallLimiter 承担并如实上报
    budget = re_mod.Budget.from_config(re_mod.load_config())
    assert index['model_calls']['limit'] == budget.max_concurrent_model_calls
    assert index['model_calls']['peak_concurrent'] <= budget.max_concurrent_model_calls
    assert index['model_calls']['total_calls'] > 0  # B 桩的 execute 都占了槽位
    assert index['episode_workers'] >= 1
    # 隔离：数据区审计 green；边界状态如实镜像 config（Q3 冻结后为已配置的 sandbox）
    assert index['isolation']['data_area']['status'] == 'green'
    configured = re_mod.isolation.boundary_report(re_mod.load_config())['enforced']
    assert index['isolation']['boundary']['enforced'] is configured


def test_gold_never_appears_in_question_only_or_episodes(chain):
    gold = [json.loads(line) for line in
            (chain / 'evaluator-only' / 'gold' / 'officeqa_pro_v2.gold.jsonl').read_text(encoding='utf-8').splitlines()]
    answers = [row['answer'] for row in gold]
    source_names = [name for row in gold for name in row['source_files']]
    # question_only：gold 答案与逐题来源映射都不得出现（协议 §6.4）。
    for path in (chain / 'question_only').rglob('*'):
        if path.is_file():
            blob = path.read_bytes()
            for needle in answers + source_names:
                assert needle.encode('utf-8') not in blob, f'{needle!r} leaked into {path}'
    # episodes：gold 答案不得出现。solver 自行找到并接纳的语料文档名属于正常
    # 工作区内容，不是逐题 gold 映射，因此只扫答案串。
    for path in (chain / 'episodes').rglob('*'):
        if path.is_file():
            blob = path.read_bytes()
            for needle in answers:
                assert needle.encode('utf-8') not in blob, f'{needle!r} leaked into {path}'


def test_freeze_seals_bytes_and_score_reports_stub_accuracy(chain):
    frozen = chain / 'evaluator-only' / 'frozen' / 'chain'
    manifest = json.loads((frozen / 'manifest.json').read_text(encoding='utf-8'))
    predictions = [json.loads(line) for line in (frozen / 'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(predictions) == 4 and manifest['predictions'] == 4
    for prediction in predictions:
        if prediction['answer_file'] is None:
            continue
        payload = Path(prediction['answer_file']).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == prediction['answer_sha256']
        assert prediction['answer_file'].startswith(str(frozen))  # 评分只依赖冻结副本
    summary = json.loads((chain / 'evaluator-only' / 'scores' / 'chain' / 'summary.json').read_text(encoding='utf-8'))
    assert summary['dry_run'] is True and summary['tolerance'] == 0.0
    for arm in ('A', 'B'):
        arm_stats = summary['arms'][arm]
        assert arm_stats['N'] == 2 and arm_stats['correct'] == 0  # stub 合成答案对合成 gold 必然 0 分
        assert arm_stats['valid_answer_rate'] == 1.0
    scores = [json.loads(line) for line in
              (chain / 'evaluator-only' / 'scores' / 'chain' / 'scores.jsonl').read_text(encoding='utf-8').splitlines()]
    assert all(row['outcome'] == 'answered' and row['score'] == 0.0 for row in scores)


def test_runner_sources_never_reference_restricted_data():
    """runner 源码不直接引用数据集凭据或原 datasets 目录；凭据剥离只在 isolation 模块。"""
    source = (EXPERIMENT / 'run_episodes.py').read_text(encoding='utf-8')
    for forbidden in ('HF_TOKEN', 'datasets/officeqa-pro-v2'):
        assert forbidden not in source, forbidden


def _unfrozen_config(tmp_path):
    """Q2 形态的未冻结 config（null 占位），用于证明新门在冻结前拒跑。"""
    config = re_mod.load_config()
    config['baseline']['integration_commit'] = None
    config['runtime'] = {'host': None, 'model': None, 'reviewer_model': None,
                         'reasoning_effort': None}
    config['budget']['search_budget'] = None
    config['isolation']['solver_boundary'] = None
    config['web_search']['entrypoint'] = None
    path = tmp_path / 'unfrozen-config.json'
    path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    return str(path)


def test_run_without_dry_run_refuses_unfrozen_config_with_zero_side_effects(chain, tmp_path, capsys):
    """真实执行门 = config 冻结校验：未冻结 config 拒跑且零副作用（协议 §11 问 10）。"""
    before = sorted(str(path) for path in (chain / 'episodes').rglob('*'))
    assert re_mod.main(['run', '--data-root', str(chain), '--config', _unfrozen_config(tmp_path),
                        '--run-label', 'gated', '--cases', '1']) == 2
    assert re_mod.main(['run', '--data-root', str(chain), '--config', _unfrozen_config(tmp_path),
                        '--run-label', 'gated', '--cases', '1', '--seed', '1']) == 2  # 传种子也救不了冻结门
    message = capsys.readouterr().err
    assert 'config 冻结校验未通过' in message and 'null/TODO' in message
    after = sorted(str(path) for path in (chain / 'episodes').rglob('*'))
    assert before == after


def test_config_freeze_errors_name_every_pending_field():
    """冻结校验逐项点名：null 扫描 + 各冻结字段的结构要求。"""
    config = re_mod.load_config()
    if not re_mod.config_freeze_errors(config):
        pytest.skip('config.json 已冻结（Q3 之后），未冻结形态用副本构造')
    config = json.loads(json.dumps(config))  # 深拷贝后逐一破坏
    errors = re_mod.config_freeze_errors(config)
    assert errors, '未冻结 config 必须给出错误清单'
    assert any('null/TODO' in error for error in errors)


def test_config_freeze_validation_passes_on_a_frozen_shape():
    """冻结形态的 config 通过校验——包括 integration_commit 为当前 HEAD 的代码态检查。"""
    config = json.loads(json.dumps(re_mod.load_config()))
    import subprocess as sp
    head = sp.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    if head.returncode != 0:
        pytest.skip('不在 git 工作树内')
    config['_notes'] = 'frozen for the test shape'  # 注释文字也不得含 TODO（§11 问 10）
    config['baseline']['integration_commit'] = head.stdout.strip()
    config['runtime'] = {'host': 'opencode', 'model': 'opencode-go/deepseek-v4.1-flash',
                         'reviewer_model': 'opencode-go/deepseek-v4.1-flash',
                         'reasoning_effort': 'high', 'note': 'x'}
    config['budget']['search_budget'] = {'search_requests': 30, 'candidate_urls': 150, 'source_pages': 60}
    config['isolation']['solver_boundary'] = {'kind': 'sandbox', 'detail': 'seatbelt profile at <data>/solver-boundary/solver.sb'}
    config['web_search']['entrypoint'] = 'offline'
    config['dataset']['revision'] = 'a' * 64
    config['dataset']['eligible_questions'] = {'dev_pilot': {'case_keys': ['b' * 64]}}
    config['dataset']['exposure_ledger'] = {'path': 'question_only/exposure_ledger.json'}
    assert re_mod.config_freeze_errors(config) == []
    # 逐项破坏都要被点名
    broken = json.loads(json.dumps(config))
    broken['runtime']['host'] = 'codex'
    assert any('runtime.host' in error for error in re_mod.config_freeze_errors(broken))
    broken = json.loads(json.dumps(config))
    broken['budget']['search_budget']['search_requests'] = 'many'
    assert any('budget.search_budget' in error for error in re_mod.config_freeze_errors(broken))
    broken = json.loads(json.dumps(config))
    broken['isolation']['solver_boundary'] = {'kind': 'chmod-only', 'detail': 'x'}
    assert any('solver_boundary' in error for error in re_mod.config_freeze_errors(broken))
    broken = json.loads(json.dumps(config))
    broken['dataset']['dev_corpus'] = {'name': None, 'revision': None, 'documents': None,
                                       'source_root': None}
    errors = re_mod.config_freeze_errors(broken)
    for field in ('dev_corpus.name', 'dev_corpus.revision', 'dev_corpus.documents',
                  'dev_corpus.source_root'):
        assert any(field in error for error in errors), field  # 四个冻结字段逐一点名


def test_score_refuses_without_freeze(chain):
    assert re_mod.main(['run', '--data-root', str(chain), '--run-label', 'nofreeze',
                        '--cases', '1', '--dry-run']) == 0
    assert re_mod.main(['score', '--data-root', str(chain), '--run-label', 'nofreeze']) == 2
    assert not (chain / 'evaluator-only' / 'scores' / 'nofreeze').exists()


def test_submission_box_validates_sequences_and_deadline(tmp_path):
    box = re_mod.SubmissionBox(tmp_path / 'episode', deadline=time.time() + 60)
    valid = json.dumps({'schema_version': 'officeqa.answer.v1', 'status': 'answered',
                        'answer': '42'}).encode()
    first = box.accept(valid, source='solver')
    assert first['accepted'] and first['seq'] == 1
    # 重复键/多对象/标签注入都只是记录在案的失败尝试，不占顺序号
    for bad in (b'{"schema_version": "x", "schema_version": "y"}',
                b'{"a": 1} {"b": 2}',
                json.dumps({'schema_version': 'officeqa.answer.v1', 'status': 'answered',
                            'answer': '<FINAL_ANSWER>42</FINAL_ANSWER>'}).encode()):
        rejected = box.accept(bad, source='solver')
        assert rejected['accepted'] is False and 'seq' not in rejected
    assert len(box.entries()) == 4 and len(box.entries(accepted_only=True)) == 1
    # 截止后到达的有效提交被拒，评分回退到此前已接纳答案（协议 §8.3）
    late = re_mod.SubmissionBox(tmp_path / 'episode-late', deadline=time.time() - 1)
    refused = late.accept(valid, source='solver')
    assert refused['accepted'] is False and 'after deadline' in refused['reason']
    assert late.chosen() is None
    assert (tmp_path / 'episode-late' / 'attempts' / '0001-answer.json').read_bytes() == valid
    second = box.accept(json.dumps({'schema_version': 'officeqa.answer.v1', 'status': 'abstained',
                                    'answer': None}).encode(), source='revision')
    assert second['seq'] == 2 and box.chosen()['seq'] == 2


def test_format_repair_is_bounded_and_gold_blind(tmp_path):
    """§4：修复只给 gold-blind 反馈、预算整集共用、超限即拒绝。"""
    box = re_mod.SubmissionBox(tmp_path / 'episode', deadline=time.time() + 60, repair_budget=1)
    bad = json.dumps({'schema_version': 'officeqa.answer.v1', 'status': 'answered',
                      'answer': '<FINAL_ANSWER>42</FINAL_ANSWER>'}).encode()
    first = box.accept(bad, source='solver')
    feedback = box.offer_format_repair(first)
    assert feedback and '格式修复（第 1/1 次' in feedback and first['reason'] in feedback
    assert box.repairs_used == 1
    # 第二次违规提交：预算耗尽，不再发反馈
    again = box.accept(b'{"a": 1}', source='solver')
    assert box.offer_format_repair(again) is None and box.repairs_used == 1
    # 有效提交与截止后提交都不触发修复
    good = json.dumps({'schema_version': 'officeqa.answer.v1', 'status': 'answered',
                       'answer': '42'}).encode()
    assert box.offer_format_repair(box.accept(good, source='solver')) is None
    expired = re_mod.SubmissionBox(tmp_path / 'episode2', deadline=time.time() - 1, repair_budget=1)
    late_bad = expired.accept(bad, source='solver')
    assert expired.offer_format_repair(late_bad) is None


def test_submit_directory_drain_dedupes_and_accepts_new_bytes(tmp_path):
    """§8.3 文件端点：同字节不重复接纳，覆盖新字节算一次新提交。"""
    box = re_mod.SubmissionBox(tmp_path / 'episode', deadline=time.time() + 60)
    submit = tmp_path / 'episode' / 'submit'
    submit.mkdir(parents=True)
    first = json.dumps({'schema_version': 'officeqa.answer.v1', 'status': 'answered',
                        'answer': '11'}).encode()
    (submit / 'answer.json').write_bytes(first)
    entries = box.drain_submit_directory(submit)
    assert len(entries) == 1 and entries[0]['source'] == 'submit-dir:answer.json'
    assert box.drain_submit_directory(submit) == []  # 同字节重复 drain 不再接纳
    revised = json.dumps({'schema_version': 'officeqa.answer.v1', 'status': 'answered',
                          'answer': '22'}).encode()
    (submit / 'answer.json').write_bytes(revised)
    entries = box.drain_submit_directory(submit)
    assert [entry['seq'] for entry in entries] == [2]  # 覆盖新字节 = 新提交
    assert box.chosen()['seq'] == 2


def test_model_call_limiter_caps_active_calls_not_threads():
    """§8.1：上限管的是“同时活跃模型调用”，超限的调用阻塞等待而不是放行。"""
    limiter = re_mod.ModelCallLimiter(2)
    inside = threading.Semaphore(0)
    release = threading.Event()

    def hold():
        with limiter.slot():
            inside.release()
            assert release.wait(timeout=10)

    holders = [threading.Thread(target=hold) for _ in range(2)]
    for thread in holders:
        thread.start()
    assert inside.acquire(timeout=10) and inside.acquire(timeout=10)
    assert limiter.report()['peak_concurrent'] == 2
    blocked: list[bool] = []

    def wait_for_slot():
        with limiter.slot():
            blocked.append(True)

    third = threading.Thread(target=wait_for_slot)
    third.start()
    third.join(0.3)
    assert not blocked and limiter.report()['peak_concurrent'] == 2  # 第三个调用在等槽位
    release.set()
    for thread in holders:
        thread.join(timeout=10)
    third.join(timeout=10)
    assert blocked and limiter.report()['total_calls'] == 3
    assert limiter.report()['peak_concurrent'] == 2  # 峰值从未超过上限
    with pytest.raises(re_mod.RunnerError):
        re_mod.ModelCallLimiter(0)


def test_solver_environment_strips_dataset_credentials(monkeypatch):
    import isolation

    monkeypatch.setenv('HF_TOKEN', 'secret-token')
    monkeypatch.setenv('HUGGING_FACE_HUB_TOKEN', 'another-secret')
    assert isolation.present_credentials() == ['HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN']
    cleaned = isolation.solver_environment()
    assert 'HF_TOKEN' not in cleaned and 'HUGGING_FACE_HUB_TOKEN' not in cleaned
    assert isolation.present_credentials(cleaned) == []
    monkeypatch.delenv('HF_TOKEN')
    monkeypatch.delenv('HUGGING_FACE_HUB_TOKEN')
    assert isolation.solver_environment().get('PATH') == isolation.solver_environment(os.environ).get('PATH')


def test_data_area_audit_fails_closed_on_permission_drift(tmp_path):
    import isolation

    (tmp_path / 'gated').mkdir(mode=0o700)
    (tmp_path / 'evaluator-only').mkdir(mode=0o700)
    (tmp_path / 'evaluator-only' / 'gold').mkdir(mode=0o700)
    (tmp_path / 'evaluator-only' / 'gold' / 'g.jsonl').write_text('{}\n')
    os.chmod(tmp_path / 'evaluator-only' / 'gold' / 'g.jsonl', 0o600)
    assert isolation.audit_data_area(tmp_path)['status'] == 'green'
    # gold 文件变成组/世界可读 → red
    os.chmod(tmp_path / 'evaluator-only' / 'gold' / 'g.jsonl', 0o644)
    report = isolation.audit_data_area(tmp_path)
    assert report['status'] == 'red' and any('group/world' in failure['error']
                                             for failure in report['failures'])
    os.chmod(tmp_path / 'evaluator-only' / 'gold' / 'g.jsonl', 0o600)
    # gated 目录变宽 → red
    os.chmod(tmp_path / 'gated', 0o755)
    assert isolation.audit_data_area(tmp_path)['status'] == 'red'
    os.chmod(tmp_path / 'gated', 0o700)
    # solver 可见目录里出现指向数据区之外的符号链接 → red
    (tmp_path / 'question_only').mkdir()
    outside_dir = tmp_path.parent / (tmp_path.name + '-outside')
    outside_dir.mkdir()
    outside = outside_dir / 'secret.csv'
    outside.write_text('uid,answer\n0,leak\n')
    (tmp_path / 'question_only' / 'escaped.json').symlink_to(outside)
    report = isolation.audit_data_area(tmp_path)
    assert report['status'] == 'red' and any('escapes the data area' in failure['error']
                                             for failure in report['failures'])


def test_boundary_report_demands_a_real_os_boundary(tmp_path):
    import isolation

    assert isolation.boundary_report({})['enforced'] is False
    assert isolation.boundary_report({'isolation': {'solver_boundary': None}})['enforced'] is False
    assert isolation.boundary_report({'isolation': {'solver_boundary': {
        'kind': 'chmod-only'}}})['enforced'] is False  # 权限位不是同用户边界
    enforced = isolation.boundary_report({'isolation': {'solver_boundary': {
        'kind': 'dedicated-user', 'detail': 'officeqa-solver'}}})
    assert enforced['enforced'] is True
    # green 数据区 + 无边界配置：真实执行前的断言仍要失败关闭
    (tmp_path / 'gated').mkdir(mode=0o700)
    (tmp_path / 'evaluator-only').mkdir(mode=0o700)
    with pytest.raises(isolation.IsolationError, match='solver_boundary'):
        isolation.assert_real_run_isolation({'isolation': {'solver_boundary': None}}, tmp_path)


def test_run_refuses_when_data_area_permissions_drift(chain, tmp_path):
    gated = chain / 'gated'
    original = stat.S_IMODE(gated.stat().st_mode)
    os.chmod(gated, 0o755)
    try:
        assert re_mod.main(['run', '--data-root', str(chain), '--run-label', 'drift',
                            '--cases', '1', '--dry-run']) == 2
        assert not (chain / 'episodes' / 'drift').exists()  # 审计失败零副作用
    finally:
        os.chmod(gated, original)


# --- Q3：同额搜索预算（§8.1）在工具层执行 ----------------------------------------


def test_corpus_search_budget_guard_caps_and_journals(chain, tmp_path):
    budget_file = tmp_path / 'episode' / 'corpus-budget.jsonl'
    run = lambda extra: ca.main(['search', '--data-root', str(chain),
                                 '--query', 'customs revenue'] + extra)
    for _ in range(2):
        assert run(['--budget-file', str(budget_file), '--search-budget', '2']) == 0
    assert ca.main(['search', '--data-root', str(chain), '--query', 'customs revenue',
                    '--budget-file', str(budget_file), '--search-budget', '2']) == 2  # 超限拒绝
    usage = ca.budget_usage(budget_file)
    assert usage == {'search': 2}
    # 无预算文件 → 不设限（dry-run/探针路径）
    assert run([]) == 0


def test_corpus_page_budget_guard_counts_page_views(chain, tmp_path):
    import contextlib
    import io
    budget_file = tmp_path / 'episode' / 'corpus-budget.jsonl'
    # 用 docs 命令找一个真实文档名（不依赖答案信息）
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert ca.main(['docs', '--data-root', str(chain)]) == 0
    name = json.loads(buffer.getvalue())[0]['name']
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert ca.main(['page', '--data-root', str(chain), '--doc', name, '--page-index', '0',
                        '--budget-file', str(budget_file), '--page-budget', '1']) == 0
        refused = ca.main(['page', '--data-root', str(chain), '--doc', name, '--page-index', '0',
                           '--budget-file', str(budget_file), '--page-budget', '1'])
    assert refused == 2  # 第二次按页查看被拒
    assert ca.budget_usage(budget_file) == {'page': 1}


def test_corpus_tool_instructions_embed_the_shared_budget(chain, tmp_path):
    budget_file = tmp_path / 'episode' / 'corpus-budget.jsonl'
    instructions = re_mod.corpus_tool_instructions(
        chain, search_budget={'search_requests': 30, 'candidate_urls': 150, 'source_pages': 60},
        budget_file=budget_file)
    assert f'--budget-file {budget_file} --search-budget 30' in instructions
    assert f'--budget-file {budget_file} --page-budget 60' in instructions
    assert '两组同额同工具' in instructions
    # 无预算时指令保持原样（dry-run 路径不撒谎）
    assert '--budget-file' not in re_mod.corpus_tool_instructions(chain)


# --- Q3：开发 6 题（evaluator 选 key，题面走 sanitization 门） --------------------


def test_dev_pilot_marks_and_sanitized_question_view(chain, tmp_path):
    """dev-pilot：key 不在池内失败关闭；合法 key 产出脱敏题面 + ledger dev 标记。"""
    ledger = json.loads((chain / 'question_only' / 'exposure_ledger.json').read_text(encoding='utf-8'))
    historical_revision = ledger['historical_revision']
    rows = [json.loads(line) for line in
            (chain / 'evaluator-only' / 'gold' / 'officeqa_pro_v1.gold.jsonl').read_text().splitlines() if line.strip()]
    keys = sorted(row['case_key'] for row in rows)[:1]
    assert pd.mark_dev_pilot(chain / 'gated', chain, list(keys))['marked'] == 1
    view = [json.loads(line) for line in
            (chain / 'question_only' / 'dev_pilot.jsonl').read_text().splitlines() if line.strip()]
    assert [row['case_key'] for row in view] == keys
    assert set(view[0]) == set(pd.QUESTION_FIELDS)
    assert view[0]['dataset'] == pd.HISTORICAL_DATASET
    ledger = json.loads((chain / 'question_only' / 'exposure_ledger.json').read_text(encoding='utf-8'))
    assert ledger['dev_pilot']['case_keys'] == keys
    # gold 答案不随题面泄出
    answers = [row['answer'] for row in rows]
    blob = (chain / 'question_only' / 'dev_pilot.jsonl').read_bytes()
    for answer in answers:
        assert answer.encode('utf-8') not in blob
    # runner 视角：dev 池可载入且 exposure=dev
    cases = re_mod.load_cases(chain)
    dev = [case for case in cases if case.exposure == 'dev']
    assert [case.case_key for case in dev] == keys
    selected = re_mod._select_cases(cases, limit=None, case_keys=[], pool='dev')
    assert [case.case_key for case in selected] == keys
    # 未知 key 失败关闭
    with pytest.raises(pd.PrepareError):
        pd.mark_dev_pilot(chain / 'gated', chain, ['f' * 64])


# --- Q3：真实传输（零模型调用，用假宿主二进制验证进程语义） -----------------------


def _fake_host(tmp_path, body: str):
    """一个可执行假 opencode：测试 turn 的 argv/超时/输出捕获，绝不连模型。"""
    binary = tmp_path / 'fake-opencode'
    binary.write_text('#!/bin/sh\n' + body, encoding='utf-8')
    binary.chmod(0o755)
    return binary


def test_opencode_run_solver_turn_argv_deadline_and_capture(chain, tmp_path):
    """A 组真实传输：shim argv、prompt 落盘、超时杀进程组、stdout 捕获、占槽。"""
    fake = _fake_host(tmp_path, 'echo "fake-host $1 $2"\nexit 0\n')
    shim = tmp_path / 'shim-opencode'
    shim.write_text('#!/bin/sh\nexec "$FAKE_HOST_BIN" "$@"\n', encoding='utf-8')
    shim.chmod(0o755)
    real = re_mod.RealContext(model='opencode-go/deepseek-v4.1-flash', variant='high',
                              host_version='fake 1.18.30', shim=shim,
                              solver_env={**os.environ, 'FAKE_HOST_BIN': str(fake)})
    workspace = tmp_path / 'ep' / 'workspace'
    workspace.mkdir(parents=True)
    limiter = re_mod.ModelCallLimiter(4)
    solver = re_mod.OpencodeRunSolver(real, workspace, limiter)
    solver._write_host_config(tmp_path / 'ep')
    host_config = json.loads((workspace / 'opencode.json').read_text(encoding='utf-8'))
    assert host_config['permission']['bash'] == 'allow'
    assert host_config['permission']['websearch'] == 'deny'
    record = solver.turn('PROMPT-TEXT', title='t', deadline=time.time() + 30)
    assert record['returncode'] == 0 and record['deadline_hit'] is False
    argv = record['argv']
    assert argv[:6] == [str(shim), 'run', '--dir', str(workspace),
                        '--model', 'opencode-go/deepseek-v4.1-flash']
    assert argv[argv.index('--variant') + 1] == 'high'
    assert 'PROMPT-TEXT' not in record['argv']  # prompt 不进 argv 记录（落盘为准）
    assert limiter.report()['total_calls'] == 1
    stdout = (workspace / 'opencode-main.stdout').read_text(encoding='utf-8')
    assert 'fake-host run --dir' in stdout


def test_opencode_run_solver_kills_past_deadline(tmp_path):
    """超deadline：进程组被杀，记录 deadline_hit，不抛异常（§8.3 截止即终点）。"""
    fake = _fake_host(tmp_path, 'sleep 30\n')
    shim = tmp_path / 'shim-opencode'
    shim.write_text('#!/bin/sh\nexec "$FAKE_HOST_BIN" "$@"\n', encoding='utf-8')
    shim.chmod(0o755)
    real = re_mod.RealContext(model='p/m', variant=None, host_version='fake', shim=shim,
                              solver_env={**os.environ, 'FAKE_HOST_BIN': str(fake)})
    workspace = tmp_path / 'ep2' / 'workspace'
    workspace.mkdir(parents=True)
    solver = re_mod.OpencodeRunSolver(real, workspace, re_mod.ModelCallLimiter(1))
    started = time.time()
    record = solver.turn('go', title='t', deadline=started + 1.5)
    assert record['deadline_hit'] is True
    assert time.time() - started < 20  # 没有陪着 sleep 30 跑完


def test_slot_accounted_runtime_wraps_execute(chain, tmp_path):
    """B 组包装：每个 execute 占一个 §8.1 槽位，其余属性透传。"""

    class Inner:
        backends = {'opencode': object()}
        cancelled = threading.Event()

        def execute(self, job, prompt, folder, on_tick=lambda: None, **kw):
            return {'job': job['id'], 'prompt_chars': len(prompt)}

    limiter = re_mod.ModelCallLimiter(2)
    wrapped = re_mod.SlotAccountedRuntime(Inner(), limiter)
    assert wrapped.execute({'id': 'job_x'}, 'hello', '/tmp') == {'job': 'job_x', 'prompt_chars': 5}
    assert limiter.report()['total_calls'] == 1
    assert wrapped.backends is not None and wrapped.cancelled is not None  # __getattr__ 透传


def test_prepare_solver_boundary_probes_fail_closed(chain, tmp_path, monkeypatch):
    """边界准备：profile/shim 落盘、活动探针证明围栏生效、PATH 注入 shim 目录。"""
    if not Path(re_mod.SANDBOX_EXEC).is_file():
        pytest.skip('本机无 sandbox-exec（非 macOS seatbelt 环境）')
    fake = _fake_host(tmp_path, 'echo fake-opencode-9.9.9\n')
    monkeypatch.setattr(re_mod.shutil, 'which', lambda name: str(fake) if name == 'opencode' else None)
    original_path = os.environ['PATH']
    config = {'dataset': {'source_root': str(chain / 'gated')},
              'isolation': {'solver_boundary': {'kind': 'sandbox', 'detail': 'test seatbelt'}}}
    try:
        probe = re_mod.prepare_solver_boundary(chain, config)
    finally:
        os.environ['PATH'] = original_path
    assert probe['probe:gated']['denied'] is True
    assert probe['probe:evaluator-only']['denied'] is True
    assert probe['probe:profile-readable']['denied'] is False
    shim = Path(probe['shim'])
    assert shim.is_file() and shim.stat().st_mode & stat.S_IXUSR
    profile = Path(probe['profile']).read_text(encoding='utf-8')
    assert '(allow default)' in profile and 'deny file-read*' in profile


# --- v1 语料接入：按题集路由（dev pilot → corpus-v1，主测试 → corpus） -------------


def _stage_dev_corpus(chain, tmp_path):
    """在 chain 数据区旁接入合成 v1 纯文本语料（与真实接入同一 staging/索引路径）。"""
    source = tmp_path / 'v1src'
    source.mkdir(parents=True, exist_ok=True)
    (source / 'treasury_bulletin_1941_01.txt').write_text(
        'TREASURY BULLETIN\nJANUARY 1941\n'
        'Expenditures for national defense calendar year 1940 totaled 7,327 million dollars\n'
        'Public debt operations by month\n', encoding='utf-8')
    ca.stage_documents(source, chain / 'corpus-v1', fmt='v1')
    ca.build_index(chain / 'corpus-v1', fmt='v1')


def _mark_one_dev_case(chain):
    """从 ledger 的历史池标 1 个 dev 题（不接触 evaluator-only gold）。"""
    ledger = json.loads((chain / 'question_only' / 'exposure_ledger.json').read_text(encoding='utf-8'))
    revision = ledger['historical_revision']
    keys = [pd.case_key(revision, entry['uid'], entry['question_sha256'])
            for entry in ledger['historical_exposed_pool']]
    assert pd.mark_dev_pilot(chain / 'gated', chain, keys[:1])['marked'] == 1
    return keys[0]


def test_corpus_selection_and_per_corpus_instructions(chain, tmp_path):
    """路由表：v2→corpus，v1→config 指名的 dev 语料；无配置时 v1 数据集无映射（fail closed）。"""
    config = re_mod.load_config()
    selection = re_mod.corpus_selection(config)
    assert selection[pd.DATASET] == 'corpus'
    assert selection[pd.HISTORICAL_DATASET] == 'corpus-v1'
    assert re_mod.corpus_selection({}) == {pd.DATASET: 'corpus'}  # 未配置 dev_corpus：v1 不回落
    _stage_dev_corpus(chain, tmp_path)
    v1 = re_mod.corpus_tool_instructions(chain, corpus_name='corpus-v1')
    assert '--corpus corpus-v1' in v1 and str(re_mod.CORPUS_TOOL_PATH) in v1
    assert '--page-index' not in v1 and '--page-budget' not in v1  # v1 无按页查看
    assert 'not_applicable' in v1 and '纯文本整档' in v1
    assert 'v1 全量纯文本，1 份整档文档' in v1  # 文档数来自所选语料的 manifest
    v2 = re_mod.corpus_tool_instructions(chain)
    assert '--corpus corpus --query' in v2 and '--page-index P' in v2
    assert '全量解析，2 份文档' in v2
    # 预算旗标按语料生成：v1 只带检索预算，v2 检索+按页都带
    budget = {'search_requests': 30, 'candidate_urls': 150, 'source_pages': 60}
    budget_file = tmp_path / 'episode' / 'corpus-budget.jsonl'
    v1_budgeted = re_mod.corpus_tool_instructions(chain, corpus_name='corpus-v1',
                                                  search_budget=budget, budget_file=budget_file)
    v2_budgeted = re_mod.corpus_tool_instructions(chain, search_budget=budget, budget_file=budget_file)
    assert f'--budget-file {budget_file} --search-budget 30' in v1_budgeted
    assert '--page-budget' not in v1_budgeted
    assert f'--budget-file {budget_file} --page-budget 60' in v2_budgeted


def test_dev_pilot_episode_routes_to_the_dev_corpus(chain, tmp_path):
    """端到端 dry-run：dev 题集双组都路由 corpus-v1，episode_record 记录所用语料。"""
    import sqlite3
    _stage_dev_corpus(chain, tmp_path)
    dev_key = _mark_one_dev_case(chain)
    assert re_mod.main(['run', '--data-root', str(chain), '--run-label', 'devroute',
                        '--pool', 'dev', '--dry-run', '--seed', '5']) == 0
    index = json.loads((chain / 'episodes' / 'devroute' / 'run_index.json').read_text(encoding='utf-8'))
    assert len(index['episodes']) == 2 and not index['failures']
    assert [case['case_key'] for case in index['cases']] == [dev_key]
    for case in index['cases']:
        assert case['dataset'] == pd.HISTORICAL_DATASET and case['corpus'] == 'corpus-v1'
    for episode in index['episodes']:
        assert episode['corpus'] == {'name': 'corpus-v1', 'format': 'v1', 'documents': 1}
        if episode['arm'] == 'A':
            prompt = (Path(episode['workspace']) / 'workspace' / 'prompt.md').read_text(encoding='utf-8')
            assert '--corpus corpus-v1' in prompt and '--page-index' not in prompt
        else:
            connection = sqlite3.connect(Path(episode['workspace']) / 'workspace' / 'briefloop.db')
            requirements = json.loads(connection.execute('SELECT requirements FROM runs').fetchone()[0])
            connection.close()
            assert '--corpus corpus-v1' in requirements['raw_input']
            assert requirements['raw_input'] == re_mod.corpus_tool_instructions(
                chain, corpus_name='corpus-v1')  # A/B 看到同一语料面（§6.1）
    # stub 真的在 v1 语料上工作：B 组检索并接纳了 corpus-v1 的文档
    arm_b = next(e for e in index['episodes'] if e['arm'] == 'B')
    assert any(call['op'] == 'search' for call in arm_b['tool_calls'])
    accepted = [call for call in arm_b['tool_calls'] if call['op'] == 'accept']
    assert accepted and accepted[0]['args']['doc'] == 'treasury_bulletin_1941_01'


def test_dev_route_fails_closed_when_the_v1_index_is_missing(chain, tmp_path):
    """dev 题集需要 corpus-v1 而索引缺失：开跑前拒绝（exit 2），零 episodes 副作用。"""
    _stage_dev_corpus(chain, tmp_path)
    _mark_one_dev_case(chain)
    moved = chain / 'corpus-v1-away'
    (chain / 'corpus-v1').rename(moved)
    try:
        assert re_mod.main(['run', '--data-root', str(chain), '--run-label', 'devmissing',
                            '--pool', 'dev', '--dry-run']) == 2
        assert not (chain / 'episodes' / 'devmissing').exists()
    finally:
        moved.rename(chain / 'corpus-v1')


def test_prepare_solver_boundary_fences_the_dev_corpus_too(chain, tmp_path, monkeypatch):
    """配了 dev_corpus 时：staged corpus-v1 deny-write、v1 源目录 deny-read 进 profile。"""
    import subprocess as sp
    if not Path(re_mod.SANDBOX_EXEC).is_file():
        pytest.skip('本机无 sandbox-exec（非 macOS seatbelt 环境）')
    source = _stage_dev_corpus(chain, tmp_path) or tmp_path / 'v1src'
    fake = _fake_host(tmp_path, 'echo fake-opencode-9.9.9\n')
    monkeypatch.setattr(re_mod.shutil, 'which', lambda name: str(fake) if name == 'opencode' else None)
    original_path = os.environ['PATH']
    config = {'dataset': {'source_root': str(chain / 'gated'),
                          'dev_corpus': {'name': 'corpus-v1', 'source_root': str(source)}},
              'isolation': {'solver_boundary': {'kind': 'sandbox', 'detail': 'test seatbelt'}}}
    try:
        probe = re_mod.prepare_solver_boundary(chain, config)
    finally:
        os.environ['PATH'] = original_path
    profile = Path(probe['profile']).read_text(encoding='utf-8')
    fenced = str(chain / 'corpus-v1')
    assert f'(subpath "{fenced}")' in profile  # deny-write 保护硬链接
    denied = sp.run([re_mod.SANDBOX_EXEC, '-f', str(Path(probe['profile'])),
                     '/bin/cat', str(source / 'treasury_bulletin_1941_01.txt')],
                    capture_output=True, timeout=30)
    assert denied.returncode != 0  # 活动探针：v1 源目录在沙箱内不可读


def test_dev_pilot_coverage_gate_fails_closed(chain, tmp_path):
    """覆盖审计门：未 staged→裁决 false 且聚合不携文档名；staged→true 且运行门放行。"""
    import shutil
    # 独立数据根验证未覆盖态：chain 可能已被更早的测试 staged 过语料
    fresh = tmp_path / 'freshroot'
    fresh.mkdir()
    for name in ('question_only', 'evaluator-only', 'gated'):
        shutil.copytree(chain / name, fresh / name)
    ledger = json.loads((fresh / 'question_only' / 'exposure_ledger.json').read_text(encoding='utf-8'))
    revision = ledger['historical_revision']
    key = pd.case_key(revision, ledger['historical_exposed_pool'][0]['uid'],
                      ledger['historical_exposed_pool'][0]['question_sha256'])
    assert pd.mark_dev_pilot(fresh / 'gated', fresh, [key])['coverage_all'] is False
    cfg = {"dataset": {"dev_corpus": {"name": "corpus-v1"},
                       "eligible_questions": {"dev_pilot": {"case_keys": [key]}}}}
    audit = json.loads((fresh / 'audit' / 'dev_pilot_coverage.json').read_text(encoding='utf-8'))
    assert audit['all_covered'] is False and audit['cases'] == 1
    assert '.txt' not in json.dumps(audit)  # 聚合裁决只含布尔/计数，无文档名
    detail = json.loads((fresh / 'evaluator-only' / 'reports' / 'dev_pilot_coverage.json').read_text(encoding='utf-8'))
    assert detail['cases'][key]['missing']  # 点名缺失的详报只在 evaluator-only
    assert any('未全覆盖' in e for e in re_mod.dev_pilot_coverage_errors(fresh, cfg))
    source = tmp_path / 'v1src'
    source.mkdir()
    (source / 'treasury_bulletin_1941_01.txt').write_text('TREASURY BULLETIN\nJANUARY 1941\n', encoding='utf-8')
    ca.stage_documents(source, fresh / 'corpus-v1', fmt='v1')
    ca.build_index(fresh / 'corpus-v1', fmt='v1')
    assert pd.mark_dev_pilot(fresh / 'gated', fresh, [key])['coverage_all'] is True
    assert re_mod.dev_pilot_coverage_errors(fresh, cfg) == []
    bad_cfg = {"dataset": {"dev_corpus": {"name": "corpus-other"},
                           "eligible_questions": {"dev_pilot": {"case_keys": [key]}}}}
    assert any('语料' in e for e in re_mod.dev_pilot_coverage_errors(fresh, bad_cfg))
    (fresh / 'audit' / 'dev_pilot_coverage.json').rename(fresh / 'audit' / 'dev_pilot_coverage.bak')
    assert any('缺失' in e for e in re_mod.dev_pilot_coverage_errors(fresh, cfg))


def test_strict_surface_and_host_config(tmp_path):
    """严格面：指令含 PDF 目录与登记命令、无计量标志；权限面 web 开 + 语料目录可读。"""
    pdf_dir = tmp_path / "corpus-v2-pdf" / "documents" / "pdf"
    pdf_dir.mkdir(parents=True)
    text = re_mod.strict_pdf_instructions(pdf_dir)
    assert str(pdf_dir) in text and "register_pdf.py" in text
    assert "--search-budget" not in text and "--budget-file" not in text  # 无逐次计量
    frame = re_mod.native_frame(tmp_path / "submit", "native", pdf_dir=pdf_dir)
    assert "PDF originals" in frame and "pdftotext" in frame
    solver = object.__new__(re_mod.OpencodeRunSolver)
    solver.workspace = tmp_path / "ws"; solver.workspace.mkdir()
    solver._write_host_config(tmp_path / "ep", web=True, read_dirs=(pdf_dir,))
    cfg = json.loads((solver.workspace / "opencode.json").read_text(encoding="utf-8"))
    perm = cfg["permission"]
    assert perm["webfetch"] == "allow" and perm["websearch"] == "allow"
    assert str(pdf_dir.resolve()) + "/**" in perm["external_directory"]


def test_strict_real_gates_fail_closed(tmp_path):
    """严格门：无语料/无审计 → 拒；审计未覆盖 → 拒；齐备且一致 → 放行。"""
    cfg = {"strict_v2": {"corpus": {"documents": 1}, "web": "native", "metering": "none"}}
    assert any("未 staged" in e for e in re_mod.strict_real_gates(tmp_path, cfg))
    manifest_dir = tmp_path / "corpus-v2-pdf" / "index"; manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"format": "pdf", "documents": 1}), encoding="utf-8")
    assert any("审计缺失" in e for e in re_mod.strict_real_gates(tmp_path, cfg))
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "strict_coverage.json").write_text(json.dumps({"all_covered": False}), encoding="utf-8")
    assert any("未全覆盖" in e for e in re_mod.strict_real_gates(tmp_path, cfg))
    (tmp_path / "audit" / "strict_coverage.json").write_text(json.dumps({"all_covered": True}), encoding="utf-8")
    assert re_mod.strict_real_gates(tmp_path, cfg) == []


def test_strict_coverage_audit(tmp_path):
    """evaluator 侧覆盖审计：源 PDF 缺失→false 且详报点名；补齐→true；聚合不含文件名。"""
    gold_dir = tmp_path / "evaluator-only" / "gold"; gold_dir.mkdir(parents=True)
    key = "a" * 64
    (gold_dir / "officeqa_pro_v2.gold.jsonl").write_text(json.dumps({
        "case_key": key, "uid": "U1", "source_files": ["doc_a.txt", "doc_b.txt"]}) + "\n", encoding="utf-8")
    pdf_dir = tmp_path / "corpus-v2-pdf" / "documents" / "pdf"; pdf_dir.mkdir(parents=True)
    (pdf_dir / "doc_a.pdf").write_text("x", encoding="utf-8")
    report = pd.write_strict_coverage(tmp_path, [key])
    assert report["all_covered"] is False and report["cases"][key[:8]]["missing"] == ["doc_b.txt"]
    (pdf_dir / "doc_b.pdf").write_text("x", encoding="utf-8")
    assert pd.write_strict_coverage(tmp_path, [key])["all_covered"] is True
    audit = json.loads((tmp_path / "audit" / "strict_coverage.json").read_text(encoding="utf-8"))
    assert audit["all_covered"] is True and ".pdf" not in json.dumps(audit) and ".txt" not in json.dumps(audit)


def test_episode_token_usage_from_opencode_db(tmp_path, monkeypatch):
    """token 记账：按 episode 目录前缀聚合 A 会话与 B 各 job 会话；库缺失/空 → 不谎报。"""
    import sqlite3
    db_path = tmp_path / "opencode.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT)")
    db.execute("CREATE TABLE message (id TEXT, session_id TEXT, data TEXT)")
    ws = tmp_path / "episodes" / "run" / "A" / "key" / "workspace"
    other = tmp_path / "episodes" / "run" / "B" / "other" / "workspace"
    db.execute("INSERT INTO session VALUES ('s1', ?)", (str(ws),))
    db.execute("INSERT INTO session VALUES ('s2', ?)", (str(ws) + "/jobs/job1",))
    db.execute("INSERT INTO session VALUES ('s3', ?)", (str(other),))
    for sid, tokens, cost in (("s1", {"input": 100, "output": 10, "cache": {"read": 5, "write": 1}}, 0.5),
                              ("s2", {"input": 200, "output": 20, "reasoning": 3}, 0.25),
                              ("s3", {"input": 999, "output": 999}, 9.9)):
        db.execute("INSERT INTO message VALUES (?, ?, ?)", ("m" + sid, sid,
                   json.dumps({"tokens": tokens, "cost": cost})))
    db.commit(); db.close()
    monkeypatch.setattr(re_mod, "OPENCODE_DB", db_path)
    usage = re_mod.episode_token_usage(ws)
    assert usage["input"] == 300 and usage["output"] == 30 and usage["reasoning"] == 3
    assert usage["cache_read"] == 5 and usage["cache_write"] == 1
    assert abs(usage["cost"] - 0.75) < 1e-9 and usage["usage_complete"] is True
    monkeypatch.setattr(re_mod, "OPENCODE_DB", tmp_path / "absent.db")
    assert re_mod.episode_token_usage(ws) is None


def test_episode_resource_sampler_parses_ps(tmp_path, monkeypatch):
    """CPU 采样器：只统计命令行含 episode 目录的进程，TIME 解析正确，负载入样。"""
    ep = tmp_path / "ep"; ep.mkdir()
    sampler = re_mod.EpisodeResourceSampler(ep)
    fake = (" 0:42.50 python3 /x/other/thing\n"
            " 1:05.25 opencode run --dir " + str(ep) + "/workspace\n"
            " 0:10.00 bash " + str(ep) + "/workspace/do.sh\n")
    monkeypatch.setattr(re_mod.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": fake})())
    count, cpu = sampler._ps()
    assert count == 2 and abs(cpu - 75.25) < 0.01


def test_condition_drift_refuses_without_declaration():
    """§6.4 freeze guard: pinned expected_code_state refuses a moved HEAD
    unless the change is explicitly declared."""
    from experiments.officeqa_structured.run_episodes import condition_drift_error
    cfg = {"baseline": {"expected_code_state": "a" * 40}}
    assert condition_drift_error(cfg, "a" * 40, False) is None          # pinned == head
    assert condition_drift_error(cfg, "b" * 40, False) is not None      # drift refuses
    assert "条件漂移" in condition_drift_error(cfg, "b" * 40, False)
    assert condition_drift_error(cfg, "b" * 40, True) is None           # declared passes
    assert condition_drift_error({"baseline": {}}, "b" * 40, False) is None  # unpinned legacy
