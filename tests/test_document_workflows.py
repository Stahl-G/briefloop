import json

import pytest

from briefloop.store import Store
from briefloop.deliverable_spec import resolve, instructions


def test_method_is_frozen_per_run_and_shared_by_revision_and_review(tmp_path, monkeypatch):
    from briefloop import document_workflows as workflows
    from briefloop.review import _snapshot
    store = Store(tmp_path)
    source = store.add_source('Synthetic evidence', 'Project A delivered milestone 1.')
    req = {'title': 'Progress', 'objective': 'Keep the requested structure',
           'workflow_id': 'business_report', 'workflow_variant': 'work_progress',
           'workflow_snapshot': {'id': 'forged'},
           'manual_sections': ['Financing'], 'target_words': 900, 'max_words': 1000}
    run = store.create_run(req, [source['id']])
    saved = json.loads(run['requirements'])
    frozen = saved['workflow_snapshot']
    assert frozen['id'] == 'business_report' and frozen['variant'] == 'work_progress'
    assert frozen['content_hash'] and frozen['role_instructions']['writing']
    assert saved['target_words'] == 900 and saved['manual_sections'] == ['Financing']
    # Installing newer methods cannot change an already saved run or its readers.
    monkeypatch.setattr(workflows, 'freeze_workflow', lambda *a, **kw: pytest.fail('historical method reloaded'))
    brief = store.publish(run['id'], {'title': 'Progress', 'markdown': 'Milestone 1 delivered.'})
    revised = store.revise(brief['id'], 'Milestone 1 delivered. Financing: pending.')
    packet = _snapshot(store, revised['id'])
    assert packet['requirements']['workflow_snapshot'] == frozen
    for role, key in [('orchestrator', 'planning'), ('analyst', 'writing'),
                      ('revision', 'writing'), ('evaluator', 'evaluation'), ('reviewer', 'evaluation')]:
        assert frozen['role_instructions'][key] in instructions(resolve(saved), role, include_spec=False)
    assert 'workflow_snapshot' not in resolve({'title': 'Legacy', 'objective': 'Read me'})


def test_workflow_selection_is_shared_with_chat_and_preserves_legacy(tmp_path):
    from briefloop.chat_tools import workspace_action
    from briefloop.document_workflows import resolve_workflow
    store = Store(tmp_path)
    catalog = workspace_action(store, {'action': 'workflows'})['workflows']
    assert {x['id'] for x in catalog} == {'general_report', 'business_report', 'meeting_minutes', 'stock_research'}
    assert store.snapshot()['workflows'] == catalog
    assert resolve_workflow({'workflow_id': 'business_report'})['variant'] == next(
        item['default_variant'] for item in catalog if item['id'] == 'business_report') == 'decision_memo'
    assert resolve_workflow({'workflow_id': 'general_report'}, 'business_report')['id'] == 'general_report'
    legacy = resolve_workflow({'report_profile': 'industry_periodic'})
    assert (legacy['id'], legacy['variant']) == ('business_report', 'industry_periodic')
    for bad in [{'workflow_id': 'unknown'}, {'workflow_id': 'general_report', 'workflow_variant': 'work_progress'},
                {'workflow_variant': 'work_progress'}]:
        with pytest.raises(ValueError):
            store.create_run({'title': 'Bad', 'objective': 'Check', 'allow_web': True, **bad}, [])


def test_meeting_template_selects_method_but_requires_actual_material(tmp_path):
    from briefloop.document_workflows import template_workflow_hint
    from briefloop.templates import template, import_builtin
    store = Store(tmp_path)
    import_builtin(store)
    templates = store.rows("SELECT id FROM templates WHERE name='会议纪要·品牌绿'")
    selected = template(store, templates[0]['id'])
    assert template_workflow_hint(selected) == 'meeting_minutes'
    req = {'title': '纪要', 'objective': '保留更正与未定事项', 'template_id': selected['id'], 'allow_web': True}
    with pytest.raises(ValueError, match='转写或笔记'):
        store.create_run(req, [])
    assert not store.rows('SELECT id FROM runs')
    source = store.add_source('合成会议笔记', '负责人未定。原定周五交付，后确认改为下周一。')
    saved = json.loads(store.create_run(req, [source['id']])['requirements'])
    assert saved['workflow_id'] == 'meeting_minutes'
    assert saved['workflow_snapshot']['variant'] == 'minutes'
    assert [s['title'] for s in saved['sections']] == [s['title'] for s in selected['spec']['sections']]
    explicit = json.loads(store.create_run({**req, 'workflow_id': 'general_report'}, [source['id']])['requirements'])
    assert explicit['workflow_id'] == 'general_report'


def test_stock_template_routes_to_frozen_research_without_forced_disclosures(tmp_path):
    from briefloop.templates import import_builtin, template, export_template
    from briefloop.document_workflows import workflow_context
    store = Store(tmp_path)
    import_builtin(store)
    for row in store.rows("SELECT id FROM templates WHERE name LIKE '券商研报%'"):
        selected = template(store, row['id'])
        source = store.add_source('合成业绩披露', '收入 120 百万元，上年同期 100 百万元；全年指引维持。')
        req = {'title': '季度事件点评', 'objective': '分析收入变化；无评级要求', 'template_id': selected['id']}
        run = store.create_run(req, [source['id']])
        saved = json.loads(run['requirements'])
        frozen = saved['workflow_snapshot']
        assert frozen['id'] == 'stock_research' and frozen['variant'] == 'event_commentary'
        assert [s['title'] for s in saved['sections']] == ['核心观点', '事件回顾', '盈利预测与估值', '风险提示']
        assert '事件点评聚焦' in workflow_context(frozen, 'reviewer')
        brief = store.publish(run['id'], {'title': req['title'], 'markdown': '收入增长 20%，全年指引维持。'})
        doc = export_template(store, brief, {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '收入增长 20%。'}]}]}, {})
        from io import BytesIO
        from docx import Document
        text = ''.join(Document(BytesIO(doc)).element.itertext())
        assert '免责声明' not in text and '投资评级' not in text and '首次覆盖' not in text
    explicit = json.loads(store.create_run({**req, 'workflow_id': 'stock_research', 'workflow_variant': 'company_research'}, [source['id']])['requirements'])
    assert explicit['workflow_snapshot']['variant'] == 'company_research'
