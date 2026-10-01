"""Monthly defaults, an explained early research stop and the engine behind a chat-submitted model."""
import json

import pytest

from briefloop import research_plan
from briefloop.models import Requirements, RESEARCH_BUDGET_PRESETS, INDUSTRY_MONTHLY_LENGTH, INDUSTRY_LENGTH
from briefloop.store import Store


MONTH = {'title': 'AI 行业月报', 'objective': '梳理本月动态', 'report_profile': 'industry_periodic',
         'period_start': '2026-09-01', 'period_end': '2026-09-30'}


def test_monthly_period_gets_monthly_length_budget_and_scouts():
    spec = Requirements.model_validate(MONTH)
    assert (spec.target_words, spec.max_words) == INDUSTRY_MONTHLY_LENGTH['zh']
    assert spec.research_budget.model_dump() == RESEARCH_BUDGET_PRESETS['monthly']
    assert spec.scout_limit == 8
    week = Requirements.model_validate({**MONTH, 'title': 'AI 行业周报', 'period_end': '2026-09-07'})
    assert (week.target_words, week.max_words) == INDUSTRY_LENGTH['zh'] and week.scout_limit is None
    chosen = Requirements.model_validate({**MONTH, 'research_budget': {'search_requests': 12}, 'scout_limit': 14,
                                          'target_words': 6000})
    assert chosen.research_budget.search_requests == 12 and chosen.scout_limit == 14 and chosen.target_words == 6000
    with pytest.raises(ValueError):
        Requirements.model_validate({**MONTH, 'scout_limit': 17})


def _run_with_searches(tmp_path, used):
    store = Store(tmp_path)
    run = store.create_run({'title': '周报', 'objective': '本周动态', 'allow_web': True, 'fact_check': False,
                            'report_date': '2026-09-08'}, [], research_protocol='quality_v1')
    research_plan.freeze(store, run['id'])
    state = store.meta('research_budget:' + run['id']) or {'search_requests': 0, 'candidate_urls': [], 'source_pages': []}
    store.set_meta('research_budget:' + run['id'], {**state, 'search_requests': used})
    return store, run


def test_first_round_stop_with_most_budget_left_needs_a_reason(tmp_path):
    store, run = _run_with_searches(tmp_path, used=8)
    with pytest.raises(research_plan.AdmissionError) as refused:
        research_plan.finish_round(store, run['id'])
    assert refused.value.code == 'research_stopped_early' and '8/30' in str(refused.value)
    closed = research_plan.finish_round(store, run['id'], early_stop_reason='本周只有两件可证实的发布，均已取得原文')
    plan = research_plan.frozen(store, run['id'])
    assert plan['rounds'][closed['round_id']]['outcome']['early_stop_reason'].startswith('本周只有两件')


def test_continuing_or_unsearched_rounds_are_not_held(tmp_path):
    store, run = _run_with_searches(tmp_path, used=8)
    assert research_plan.finish_round(store, run['id'], continue_research=True)['index'] == 1
    store2, run2 = _run_with_searches(tmp_path / 'offline', used=0)
    assert research_plan.finish_round(store2, run2['id'])['index'] == 1


def test_chat_model_runs_on_the_chat_engine_not_the_workspace_default(tmp_path):
    from briefloop.chat_tools import workspace_action
    store = Store(tmp_path)
    store.update_settings({'agent_backend': 'antigravity', 'model': 'gemini-3.8-flash'})
    from briefloop.chat_store import ChatStore
    chat = ChatStore(store).create('报告对话', {'backend': 'claude', 'model': 'opus', 'effort': 'medium'}, store.root)
    source = store.add_source('公开材料', '本期公开材料。')
    result = workspace_action(store, {'action': 'generate', 'session_id': chat['id'],
                                      'requirements': {'title': '月报', 'objective': '梳理动态', 'allow_web': False, 'fact_check': False},
                                      'source_ids': [source['id']], 'runtime': {'model': 'opus', 'reasoning_effort': 'medium'}})
    payload = json.loads(store.one('jobs', result['job_id'])['payload'])
    assert payload['agent_backend'] == 'claude' and payload['runtime']['model'] == 'opus'


@pytest.mark.parametrize('period', ['2026-09', '2026年9月', '2026-09-01 至 2026-09-30'])
def test_chat_period_forms_receive_same_monthly_defaults(tmp_path, period):
    from briefloop.chat_tools import workspace_action
    store = Store(tmp_path)
    store.update_settings({'agent_backend': 'codex', 'model': 'test-model', 'model_selection_required': False})
    result = workspace_action(store, {'action': 'generate', 'requirements': {
        'title': 'AI 行业动态', 'objective': '梳理动态', 'report_profile': 'industry_periodic',
        'period': period, 'allow_web': True, 'fact_check': False}})
    requirements = json.loads(store.one('runs', result['run_id'])['requirements'])
    payload = json.loads(store.one('jobs', result['job_id'])['payload'])
    assert requirements['time_context']['start'].startswith('2026-09-01T')
    assert requirements['time_context']['end_exclusive'].startswith('2026-10-01T')
    assert requirements['research_budget'] == RESEARCH_BUDGET_PRESETS['monthly']
    assert (requirements['target_words'], requirements['max_words']) == INDUSTRY_MONTHLY_LENGTH['zh']
    assert requirements['scout_limit'] == payload['max_parallel'] == 8


def test_monthly_defaults_preserve_explicit_choices_and_case_insensitive_title():
    spec = Requirements.model_validate({'title': 'AI MONTHLY REPORT', 'objective': 'Review changes'})
    assert spec.scout_limit == 8 and spec.research_budget.search_requests == 80
    chosen = Requirements.model_validate({**MONTH, 'period': '2026-09', 'target_words': 4500, 'max_words': 5500,
        'scout_limit': 2, 'research_budget': RESEARCH_BUDGET_PRESETS['weekly']})
    assert (chosen.target_words, chosen.max_words, chosen.scout_limit) == (4500, 5500, 2)
    assert chosen.research_budget.model_dump() == RESEARCH_BUDGET_PRESETS['weekly']


def test_report_scout_limit_reaches_frozen_plan_and_native_packet(tmp_path):
    from briefloop.runtime import generation_prompt
    store = Store(tmp_path)
    store.update_settings({'model': 'test-model', 'model_selection_required': False, 'max_parallel': 12})
    run = store.create_run({**MONTH, 'scout_limit': 2, 'fact_check': False}, [], research_protocol='quality_v1')
    job = store.enqueue('generate', {'run_id': run['id']})
    plan = research_plan.freeze(store, run['id'])
    assert json.loads(job['payload'])['max_parallel'] == plan['structure']['parallel'] == 2
    assert plan['budget'] == RESEARCH_BUDGET_PRESETS['monthly']
    store.update_settings({'max_parallel': 16})
    folder = store.root / 'native-packet'
    folder.mkdir()
    generation_prompt(store, run, folder, backend='briefloop-native', scout_budget=lambda _: 1)
    packet = json.loads((folder / 'input.json').read_text())
    assert packet['max_parallel'] == len(packet['scout_slots']) == 1
    assert research_plan.frozen(store, run['id'])['frozen_runtime']['max_parallel'] == 2


@pytest.mark.parametrize(('language', 'maximum'), [('zh', 6000), ('en', 4000)])
def test_explicit_maximum_bounds_only_the_automatic_monthly_target(language, maximum):
    spec = Requirements.model_validate({**MONTH, 'language': language, 'max_words': maximum})
    assert spec.max_words == spec.target_words == maximum
    with pytest.raises(ValueError, match='长度上限不能小于目标长度'):
        Requirements.model_validate({**MONTH, 'language': language, 'max_words': maximum,
                                     'target_words': maximum + 1})


@pytest.mark.parametrize('entry', ['host', 'native'])
def test_continuation_holds_writer_until_next_round_or_explained_stop(tmp_path, entry):
    from types import SimpleNamespace
    from briefloop.chat_tools import workspace_action
    from briefloop.native_orchestrator import action
    store, run = _run_with_searches(tmp_path, used=8)
    config = {'run_id': run['id'], 'session_id': 'test',
              '_harness': SimpleNamespace(cancel_requested=lambda _: False)}
    def finish(**fields):
        request = {'action': 'finish_research_round', 'run_id': run['id'], **fields}
        return (workspace_action(store, request) if entry == 'host'
                else action(store, config, {'request': request}))
    finish(continue_research=True, summary='本轮已取得主要发布原文')
    with pytest.raises(research_plan.AdmissionError) as refused:
        research_plan.require_writing_closeout(store, run['id'])
    assert refused.value.code == 'research_continuation_pending'
    if entry == 'native':
        from briefloop.native_orchestrator import write_report
        from briefloop.native_roles import ToolError
        packet = store.root / 'writer' / 'packet'
        packet.mkdir(parents=True)
        for name in ('plan.json', 'research.json'):
            (packet.parent / name).write_text('{}')
        with pytest.raises(ToolError, match='已记录继续检索'):
            write_report(store, {**config, 'packet_root': str(packet)}, {})
    reason = '核对后没有影响结论的未覆盖方向，无需继续检索'
    finish(early_stop_reason=reason, summary='不得覆盖原收轮摘要')
    accepted = research_plan.require_writing_closeout(store, run['id'])
    assert accepted['outcome']['early_stop_reason'] == reason
    assert accepted['outcome']['summary'] == '本轮已取得主要发布原文'
    path = store.root / 'research' / run['id'] / 'rounds' / '1' / 'outcome.json'
    assert json.loads(path.read_text())['early_stop_reason'] == reason
    original = path.read_bytes()
    finish(early_stop_reason=reason)
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match='不可改写'):
        finish(early_stop_reason='different reason')
    assert store.meta('research_budget:' + run['id'])['search_requests'] == 8


def test_completed_following_round_resolves_continuation(tmp_path):
    store, run = _run_with_searches(tmp_path, used=8)
    closed = research_plan.finish_round(store, run['id'], continue_research=True,
                                        gaps=[{'description': '核对遗漏发布'}])
    research_plan.begin_round(store, run['id'], target_gap_ids=[closed['gaps'][0]['id']])
    research_plan.finish_round(store, run['id'], summary='已核对方向，保留限制')
    assert research_plan.require_writing_closeout(store, run['id'])['round_index'] == 2


@pytest.mark.parametrize('scenario', ['unexplained', 'explained', 'material_only', 'legacy'])
def test_host_draft_publication_enforces_research_continuation(tmp_path, monkeypatch, scenario):
    import threading
    from briefloop.runtime import Worker
    store = Store(tmp_path)
    store.update_settings({'model': 'test-model', 'model_selection_required': False})
    source = store.add_source('材料', '本期已有公开材料。')
    controlled = scenario in ('unexplained', 'explained')
    run = store.create_run({'title': '月报', 'objective': '梳理变化', 'allow_web': controlled, 'fact_check': False},
        [source['id']], **({'research_protocol': 'quality_v1'} if scenario != 'legacy' else {}))
    job = store.enqueue('generate', {'run_id': run['id']})
    class Host:
        cancelled = threading.Event()
        calls = 0
        def execute(self, job, prompt, folder, on_tick, **kwargs):
            self.calls += 1
            if scenario != 'legacy' and self.calls == 1:
                from briefloop.scout_coverage import declare
                declare(store, run['id'], [])
                if scenario == 'material_only':research_plan.finish_round(store, run['id'])
            if controlled and self.calls == 1:
                store.set_meta('research_budget:' + run['id'], {'search_requests': 8, 'candidate_urls': [], 'source_pages': []})
                research_plan.finish_round(store, run['id'], continue_research=True)
            if controlled and self.calls == 2:
                assert 'early_stop_reason' in prompt
                if scenario == 'explained':
                    research_plan.finish_round(store, run['id'], early_stop_reason='所有重要方向已有本期原文')
            (folder / 'draft.json').write_text(json.dumps({'title': '月报', 'markdown': '本期已有公开材料。'}))
            on_tick()
            if controlled and self.calls == 1:
                assert not store.rows('SELECT id FROM briefs')
            return {'status': 'complete'}
    host = Host()
    worker = Worker(store, host)
    monkeypatch.setattr(worker, 'complete_draft_checks', lambda job, brief, folder, **kwargs: {'version_id': brief['id']})
    if scenario == 'unexplained':
        with pytest.raises(research_plan.AdmissionError) as refused:
            worker.generate(job, score=False)
        assert refused.value.code == 'research_continuation_pending'
        assert not store.rows('SELECT id FROM briefs')
        assert (store.root / 'jobs' / job['id'] / 'draft.json').exists()
    else:
        result = worker.generate(job, score=False)
        assert store.one('briefs', result['version_id'])['markdown'] == '本期已有公开材料。'
    assert host.calls == (2 if controlled else 1)


def test_monthly_threshold_uses_calendar_days_across_dst():
    for zone in ('America/New_York', 'Etc/UTC'):
        req = Requirements(title='AI industry update', objective='Review changes',
            report_profile='industry_periodic', period_start='2026-03-01',
            period_end='2026-03-25', report_timezone=zone)
        assert req.research_budget.search_requests == 80
        assert req.scout_limit == 8
def test_provider_failures_get_actionable_fixed_text():
    from briefloop.progress import public_failure
    quota = public_failure('Antigravity: Individual quota reached. Please upgrade your subscription. Resets in 3h29m49s.')
    assert quota.startswith('模型服务额度已用完') and '约 3 小时 29 分钟后恢复' in quota and 'upgrade' not in quota
    assert public_failure('HTTP 401 Unauthorized https://api.example.com/v1?key=secret').startswith('模型服务未登录')
    assert public_failure('invalid model selection (--model "x"): --model x requires --effort').startswith('所选模型')
    assert public_failure('后台调用未绑定可处理授权的任务，已拒绝并终止本轮：Bash').startswith('执行引擎在后台请求了命令授权')
    assert public_failure(quota) == quota  # a stored job error stays readable on the task card
    assert public_failure('Traceback: something internal') is None


def test_role_model_chosen_on_another_engine_follows_the_main_model(tmp_path):
    store = Store(tmp_path)
    store.update_settings({'agent_backend': 'codex', 'model': 'gpt-6-sol', 'role_models': {'evaluator': {'model': 'gpt-6-luna'}}})
    assert store.settings()['role_models']['evaluator']['backend'] == 'codex'
    assert store.role_model_config()['evaluator']['model'] == 'gpt-6-luna'
    store.update_settings({'agent_backend': 'antigravity', 'model': 'gemini-3.8-flash'})
    assert store.role_model_config()['evaluator']['model'] == 'gemini-3.8-flash'
    store.update_settings({'agent_backend': 'codex', 'model': 'gpt-6-sol'})
    assert store.role_model_config()['evaluator']['model'] == 'gpt-6-luna'


def test_cli_hosts_review_on_their_own_permissions_and_say_so():
    from briefloop.review_capability import review_available, review_isolation, require_for_fact_check, summary
    for backend in ('claude', 'antigravity', 'codebuddy'):
        assert review_available(backend) and review_isolation(backend) == 'observed'
        require_for_fact_check(backend)  # no longer refused
        assert not review_available(backend, review_mode='strict')
    assert review_isolation('codex') == 'enforced' and review_isolation('briefloop-native', 'strict') == 'enforced'
    choices = {c['id']: c for c in summary()['review_choices']}
    assert choices['claude']['isolation'] == 'observed' and choices['codex']['isolation'] == 'enforced'
