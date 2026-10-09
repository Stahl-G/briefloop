"""Small source-bound candidate checks; controlled local runtime, no model calls."""
import copy
import json
import threading

from briefloop.deliverable_spec import (resolve, instructions, analysis_check_instructions,
                                        validate_analysis_checks, candidate_assessment)
from briefloop.models import Assessment, must_fix
from briefloop.revision_policy import revision_reasons
from briefloop.runtime import Worker, assessment_prompt
from briefloop.store import Store, dump

OBJECTIVE = '数据章列出季度指标；影响章给出本次扩容决策的观察节点。'
BODY = '## 数据\n交付 100 台，上季 90 台。\n\n## 影响\n本季度交付比上季多 10 台。'
SPEC = resolve({'title': '合成资料', 'objective': OBJECTIVE})


def passing(**changes):
    return {'brief_hash': 'unused', 'status': 'complete', 'summary': '合成评价',
            'overall': '达到要求', 'evidence': 4, 'coverage': 4, 'analysis': 4,
            'expression': 4, **changes}


def check(**changes):
    return {'chapter_quote': '## 影响', 'requirement_quote': '影响章给出本次扩容决策的观察节点',
            'expectation': 'required', 'judgment_quote': '',
            'rationale': '本章明确承担扩容观察职责，只有交付事实，尚无观察节点。', **changes}


def test_default_scores_and_prompts_keep_existing_behavior(tmp_path):
    assert Assessment.model_validate(passing()).analysis_checks == []
    assert not must_fix(passing(analysis=2))
    assert not revision_reasons(passing(analysis=2), [])
    assert must_fix(passing(expression=2))
    assert revision_reasons(passing(expression=2), [])
    assert '章级分析检查候选' not in instructions(SPEC, 'evaluator')
    assert '章级分析检查候选' in analysis_check_instructions(SPEC)
    store, _, brief, _ = saved(tmp_path)
    folder = tmp_path / 'score'; folder.mkdir()
    assert '章级分析检查候选' not in assessment_prompt(store, brief, folder)
    assert '章级分析检查候选' in assessment_prompt(store, brief, folder, analysis_checklist_candidate=True)


def test_bad_quotes_and_foreign_chapter_judgment_cannot_force_repair():
    for item in [check(chapter_quote='## 不存在'), check(requirement_quote='必须预测下季度增长'),
                 check(judgment_quote='交付 100 台，上季 90 台。'), check(requirement_quote='')]:
        value = passing(overall='建议修改', analysis=2, analysis_checks=[item], findings=[{
            'kind': 'no_implication', 'dimension': 'analysis', 'severity': 'major',
            'description': '模型声称缺判断', 'report_quote': item['chapter_quote']}])
        prepared = candidate_assessment(value, SPEC, BODY)
        assert prepared['analysis_checks'][0]['status'] == 'uncertain'
        assert prepared['analysis_checks'][0]['validation_errors']
        assert not revision_reasons(prepared, [], analysis_context={'spec': SPEC, 'markdown': BODY})
        assert prepared['analysis_check_unverified_findings'] == value['findings']


def test_fact_or_unclear_chapter_and_optional_expression_remain_optional():
    for item in [check(chapter_quote='## 数据', requirement_quote='数据章列出季度指标', expectation='not_required'),
                 check(expectation='uncertain'), check(expectation='optional')]:
        value = passing(analysis_checks=[item])
        assert not revision_reasons(value, [], analysis_context={'spec': SPEC, 'markdown': BODY})
    for kind in ('filler', 'restatement', 'off_topic'):
        assert not revision_reasons(passing(overall='建议修改', findings=[{
            'dimension': 'expression', 'severity': 'minor', 'kind': kind,
            'description': '可选精简'}]), [], analysis_context={'spec': SPEC, 'markdown': BODY})
    purpose_spec = resolve({'title': '合成', 'objective': '阅读数据', 'sections': [
        {'section_id': 'impact', 'title': '影响', 'purpose': '给出扩容观察节点'}]})
    foreign = check(chapter_quote='## 数据', requirement_quote='给出扩容观察节点')
    assert validate_analysis_checks(passing(analysis_checks=[foreign]), purpose_spec, BODY)[0]['status'] == 'uncertain'


def test_existing_judgment_does_not_become_missing():
    judgment = '是否继续扩容，应结合下一季度订单覆盖率核对。'
    body = BODY + '\n' + judgment
    value = passing(analysis_checks=[check(judgment_quote=judgment)])
    assert validate_analysis_checks(value, SPEC, body)[0]['status'] == 'judgment_present'
    assert not revision_reasons(value, [], analysis_context={'spec': SPEC, 'markdown': body})


def saved(tmp_path):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'auto_learn': False, 'company_context_enabled': False})
    source = store.add_source('合成季度材料', '交付 100 台，上季 90 台。')
    run = store.create_run({'title': '合成', 'objective': OBJECTIVE, 'fact_check': False}, [source['id']])
    brief = store.publish(run['id'], {'title': '合成', 'markdown': BODY})
    job = store.enqueue('generate', {'run_id': run['id'], 'agent_backend': 'codex',
                                    'runtime': {'model': 'controlled-local-no-api'}, 'quality_checklist_candidate': 'chapter-v1'})
    return store, run, brief, job


def test_missing_required_judgment_consumes_one_revision_without_overwriting(tmp_path):
    store, run, brief, job = saved(tmp_path)
    value = passing(brief_hash=brief['hash'], analysis_checks=[check()])
    before = copy.deepcopy(value)
    prepared = candidate_assessment(value, SPEC, brief['markdown'])
    assert prepared['findings'][0]['kind'] == 'no_implication'
    assert value == before
    assert not revision_reasons(value, [])  # Candidate is opt-in.
    assert [item['type'] for item in revision_reasons(value, [], analysis_context={'spec': SPEC, 'markdown': BODY})] == ['analysis_check']
    store.assess(brief['id'], value)

    class LocalRuntime:
        cancelled = threading.Event()
        calls = []

        def execute(self, job, prompt, folder, **kwargs):
            self.calls.append(folder.name)
            if folder.name == 'revision':
                packet = json.loads((folder / 'input.json').read_text())
                assert packet['revision_reasons'][0]['type'] == 'analysis_check'
                assert packet['assessment']['findings'][0]['kind'] == 'no_implication'
                (folder / 'draft.json').write_text(dump({'title': '合成修订', 'markdown': BODY + '\n是否继续扩容，应结合下一季度订单覆盖率核对。'}))
                (folder / 'responses.json').write_text('[]')
            else:
                packet = json.loads((folder / 'input.json').read_text())
                (folder / 'assessment.json').write_text(dump(passing(brief_hash=packet['brief']['hash'])))
            return {'status': 'complete', 'controlled_runtime': True}

    runtime = LocalRuntime(); worker = Worker(store, runtime)
    folder = worker.folder(job)
    revised = worker.auto_revise(job, brief, folder)
    assert revised['revision_status'] == 'complete'
    assert store.one('briefs', brief['id'])['markdown'] == BODY
    assert store.one('briefs', revised['version_id'])['parent_id'] == brief['id']
    assert len(store.rows('SELECT * FROM briefs WHERE run_id=?', (run['id'],))) == 2
    assert worker.auto_revise(job, brief, folder)['version_id'] == revised['version_id']
    assert runtime.calls == ['revision', 'revision-evaluation']
    assert json.loads(store.rows('SELECT data FROM assessments WHERE version_id=?', (brief['id'],))[0]['data'])['analysis_checks'] == value['analysis_checks']



def test_native_submit_and_store_preserve_candidate_fields_and_empty_is_uncovered(tmp_path):
    from briefloop.native_roles import run_tool, runner_tool_specs
    store, _, brief, _ = saved(tmp_path)
    folder = tmp_path / 'native-score'; folder.mkdir()
    assessment_prompt(store, brief, folder, 'briefloop-native', analysis_checklist_candidate=True)
    schema = json.loads((folder / 'packet' / 'assessment.schema.json').read_text())
    assert 'analysis_checks' in schema['properties']
    assert schema['properties']['analysis_checks']['items']['$ref'] == '#/$defs/AnalysisCheck'
    assert set(schema['$defs']['AnalysisCheck']['properties']) == {
        'chapter_quote', 'requirement_quote', 'expectation', 'judgment_quote', 'rationale'}
    config = {'native_role': 'evaluator', 'packet_root': str(folder / 'packet'), 'version_id': brief['id']}
    submit = next(spec for spec in runner_tool_specs('evaluator', config=config) if spec['name'] == 'submit_assessment')
    assert submit['parameters']['properties']['assessment']['type'] == 'object'
    value = passing(brief_hash=brief['hash'], analysis_checks=[check()])
    result = run_tool(store, config, 'submit_assessment', {'assessment': value})
    assert result['ok']
    settled = json.loads(result['settle'])
    artifact = json.loads((folder / 'assessment.json').read_text())
    assert settled['analysis_checks'] == artifact['analysis_checks'] == value['analysis_checks']
    row = store.assess(brief['id'], artifact)
    assert json.loads(row['data'])['analysis_checks'] == value['analysis_checks']
    empty = passing(brief_hash=brief['hash'], analysis_checks=[])
    result = run_tool(store, config, 'submit_assessment', {'assessment': empty})
    assert result['ok']  # Schema admission is not semantic or candidate coverage success.
    row = store.assess(brief['id'], json.loads((folder / 'assessment.json').read_text()))
    restored = json.loads(row['data'])
    assert restored['analysis_checks'] == []
    assert candidate_assessment(restored, SPEC, BODY)['analysis_check_coverage']['status'] == 'not_checked'
    assert not revision_reasons(restored, [], analysis_context={'spec': SPEC, 'markdown': BODY})



def test_manual_chapter_and_assignment_keep_placeholder_without_automatic_judgment():
    body = '## 数据\n本期交付数据保留。\n\n## 人工意见\n待填充'
    section_spec = resolve({'title': '合成人工章', 'objective': '整理本期数据。', 'sections': [
        {'section_id': 'manual', 'title': '人工意见', 'mode': 'manual',
         'purpose': '给出扩容观察节点，留给人工填写'}]})
    assignment_spec = resolve({'title': '合成分工', 'objective': '整理本期数据。',
                               'manual_sections': ['人工意见由人工填写，仅保留待填充']})
    alias_spec = copy.deepcopy(assignment_spec)
    next(item for item in alias_spec['requirement_items'] if item['kind'] == 'manual')['kind'] = 'manual_assignment'
    for spec, quote in [(section_spec, '给出扩容观察节点'),
                        (assignment_spec, '人工意见由人工填写，仅保留待填充'),
                        (alias_spec, '人工意见由人工填写，仅保留待填充')]:
        item = check(chapter_quote='## 人工意见', requirement_quote=quote)
        for score in [passing(analysis_checks=[item]), passing(overall='建议修改', analysis=2,
                analysis_checks=[item], findings=[{'kind': 'no_implication', 'dimension': 'analysis',
                    'severity': 'major', 'description': '模型误判人工章缺判断', 'report_quote': '待填充'}])]:
            before = copy.deepcopy(score)
            prepared = candidate_assessment(score, spec, body)
            derived = prepared['analysis_checks'][0]
            assert derived['status'] == 'manual' and derived['preservation_note']
            assert derived['expectation'] == 'required' and derived['rationale'] == item['rationale']
            assert not prepared['findings']
            assert not revision_reasons(prepared, [], analysis_context={'spec': spec, 'markdown': body})
            assert score == before and body.endswith('待填充')
    for item in [check(chapter_quote='## 人工意见', requirement_quote='从不存在的要求'),
                 check(chapter_quote='## 人工意见', requirement_quote='给出扩容观察节点',
                       judgment_quote='本期交付数据保留。')]:
        derived = validate_analysis_checks(passing(analysis_checks=[item]), section_spec, body)[0]
        assert derived['status'] == 'uncertain' and derived['validation_errors']
        assert 'preservation_note' not in derived
    # Preserve the older unstructured overall fallback. This patch does not
    # resolve why the model recommended changes or hide other real defects.
    bare = passing(overall='建议修改', analysis=2, analysis_checks=[
        check(chapter_quote='## 人工意见', requirement_quote='给出扩容观察节点')])
    derived = candidate_assessment(bare, section_spec, body)
    assert derived['analysis_checks'][0]['status'] == 'manual'
    assert revision_reasons(derived, [], analysis_context={'spec': section_spec, 'markdown': body}) == [
        {'type': 'overall', 'overall': '建议修改'}]


def test_manual_assignment_cannot_exempt_another_chapter():
    spec=resolve({'title':'合成分工','objective':'影响章给出扩容判断。',
                  'manual_sections':['人工意见由人工填写，仅保留待填充']})
    body='## 影响\n仅交付数据。\n\n## 人工意见\n待填充'
    item=check(chapter_quote='## 影响',requirement_quote='人工意见由人工填写，仅保留待填充')
    result=validate_analysis_checks(passing(analysis_checks=[item]),spec,body)[0]
    assert result['status'] == 'missing' and 'preservation_note' not in result
