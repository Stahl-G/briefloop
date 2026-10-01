"""Fast drafts run on every engine and say how their plain turns were held to the materials."""
import json
import re
from pathlib import Path

from briefloop import plain_isolation
from briefloop.runtime import Worker
from briefloop.store import Store, dump
from test_fast_reports import Writer, finish


def test_levels_runtime_and_working_directory(tmp_path):
    assert plain_isolation.runtime('codex') == {'permission': 'read-only'}
    assert plain_isolation.runtime('pi') == {'host_options': {'mode': 'none'}}
    # Bridge CLIs keep their native permission; BriefLoop does not request a mode it cannot enforce.
    assert plain_isolation.runtime('antigravity') == {}
    folder = tmp_path / 'jobs' / 'job_1' / 'fast-writing'
    assert plain_isolation.working_directory('codex', folder) == folder
    outside = plain_isolation.working_directory('antigravity', folder)
    assert outside.is_dir() and tmp_path not in outside.parents
    assert plain_isolation.working_directory('antigravity', folder) == outside


def test_frontend_lists_the_same_enforced_engines():
    source = (Path(__file__).resolve().parents[1] / 'frontend' / 'quick-report.js').read_text(encoding='utf-8')
    listed = re.search(r'ENFORCED_FAST_BACKENDS=\[([^\]]*)\]', source).group(1)
    assert tuple(re.findall(r"'([^']+)'", listed)) == plain_isolation.ENFORCED


class ObservedWriter(Writer):
    """An engine that reports one tool call while writing and one unsupported claim."""

    def execute(self, job, prompt, folder, *args, **kwargs):
        result = super().execute(job, prompt, folder, *args, **kwargs)
        if folder.name == 'fast-writing':
            (folder / 'events.jsonl').write_text(json.dumps({'type': 'item.completed', 'data': {'item': {
                'id': 't1', 'type': 'runtime_tool', 'tool': 'search_web', 'status': 'completed'}}}) + '\n')
        if folder.name == 'evidence':
            data = json.loads((folder / 'response.txt').read_text())
            data['unsupported'] = [{'report_quote': '增长原因仍需进一步材料说明。', 'reason': '材料未给出原因'},
                                   {'report_quote': '正文里没有这句', 'reason': '应被丢弃'}]
            (folder / 'response.txt').write_text(dump(data))
        return result


def test_bridge_engine_drafts_with_recorded_tool_use_and_lists_unsupported_claims(tmp_path):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'company_context_enabled': False, 'auto_learn': False})
    source = store.add_source('Synthetic quarterly disclosure', '本测试材料为虚构。\n收入 120 万元，同比增长 20%\n以上为本季度实际数。')
    run = store.create_run({'title': '季度观察', 'objective': '解释收入变化', 'allow_web': False,
                            'fact_check': False, 'completion_mode': 'fast'}, [source['id']])
    job = store.enqueue('generate', {'run_id': run['id'], 'auto_revision': True, 'agent_backend': 'antigravity',
                                     'runtime': {'model': 'gemini-3.8-flash'}})
    worker = Worker(store, ObservedWriter(store))
    store.update_job(job['id'], 'running')
    result = worker.generate(job)
    store.update_job(job['id'], 'complete', result=result)

    draft = store.one('briefs', result['version_id'])
    notes = {note['kind']: note for note in json.loads(draft['detail'])['research_notes']}
    assert notes['fast_isolation']['level'] == 'observed' and notes['fast_isolation']['tools'] == ['search_web']
    assert 'search_web' in notes['fast_isolation']['summary']
    events = [json.loads(e['data']) for e in store.rows("SELECT data FROM events WHERE kind='plain_isolation'")]
    assert events and events[0]['level'] == 'observed'

    finish(store, worker, result)
    enriched = store.one('briefs', 'brief_' + result['checks_job_id'][4:] + '_evidence')
    notes = {note['kind']: note for note in json.loads(enriched['detail'])['research_notes']}
    assert [item['report_quote'] for item in notes['fast_unsupported']['items']] == ['增长原因仍需进一步材料说明。']
