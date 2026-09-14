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
                                     'format_repair_attempts': budget.format_repair_attempts}
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
    # 隔离：数据区审计 green，边界（同用户隔离机制）如实记录为未配置
    assert index['isolation']['data_area']['status'] == 'green'
    assert index['isolation']['boundary']['enforced'] is False


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


def test_runner_has_no_real_execution_path_and_no_dataset_reference():
    source = (EXPERIMENT / 'run_episodes.py').read_text(encoding='utf-8')
    for forbidden in ('HF_TOKEN', 'datasets/officeqa-pro-v2', 'subprocess', 'codex exec', 'Popen',
                      'InteractiveRuntime'):
        assert forbidden not in source, forbidden


def test_run_without_dry_run_refuses_with_zero_side_effects(chain, tmp_path):
    before = sorted(str(path) for path in (chain / 'episodes').rglob('*'))
    assert re_mod.main(['run', '--data-root', str(chain), '--run-label', 'gated',
                        '--cases', '1']) == 2
    assert re_mod.main(['run', '--data-root', str(chain), '--run-label', 'gated',
                        '--cases', '1', '--seed', '1']) == 2  # 传种子也救不了授权门
    after = sorted(str(path) for path in (chain / 'episodes').rglob('*'))
    assert before == after


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
