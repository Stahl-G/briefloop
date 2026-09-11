"""A finished draft must survive schema drift; only the offending key is lost."""
import json
import subprocess
import sys
import pytest
from briefloop.models import BriefDraft, check_artifact, prune_unknown
from briefloop.interactive_runtime import InteractiveRuntime
from briefloop.runtime import Worker
from briefloop.store import Store
from test_interactive_runtime import FakeHarness


def make_run(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = store.add_source('输入', '指标A本期12台，上期10台。')
    run = store.create_run({'title': '周报', 'objective': '验证稿件契约'}, [source['id']])
    return store, run, source


class FakeRuntime:
    """Stands in for a model that finished its turn and saved draft.json."""

    def __init__(self, draft):
        self.draft = draft

    def execute(self, job, prompt, folder, on_tick=lambda: None, **kwargs):
        (folder / 'draft.json').write_text(json.dumps(self.draft, ensure_ascii=False))
        on_tick()
        return {}


def test_invented_draft_keys_are_dropped_and_recorded_instead_of_losing_the_report(tmp_path):
    store, run, source = make_run(tmp_path)
    job = store.enqueue('generate', {'run_id': run['id']})
    worker = Worker(store)
    worker.runtime = FakeRuntime({'title': '周报', 'markdown': '指标A为12台。', 'summary': '模型自创的键',
                                  'citations': [{'source_id': source['id'], 'confidence': 'high'}]})
    result = worker.generate(job, score=False)
    brief = store.one('briefs', result['version_id'])
    assert brief['markdown'] == '指标A为12台。'
    dropped = [json.loads(row['data'])['fields'] for row in
               store.rows("SELECT data FROM events WHERE job_id=? AND kind='draft_fields_dropped'", (job['id'],))]
    assert dropped == [['summary', 'citations.0.confidence']]
    assert 'summary' not in json.loads(brief['detail'])


def test_a_rejected_draft_names_the_field_and_keeps_the_agent_file(tmp_path):
    store, run, source = make_run(tmp_path)
    job = store.enqueue('generate', {'run_id': run['id']})
    worker = Worker(store)
    worker.runtime = FakeRuntime({'title': '周报', 'markdown': '指标A为12台。',
                                  'report_data': {'records': [{'metric': '指标A', 'unit': '台', 'current': '12 台',
                                                               'current_date': '2026-09-08',
                                                               'source_id': source['id']}]}})
    with pytest.raises(ValueError, match='report_data.records.0.current'):
        worker.generate(job, score=False)
    saved = store.root / 'jobs' / job['id'] / 'draft-invalid.json'
    assert json.loads(saved.read_text())['title'] == '周报'
    assert not store.rows('SELECT id FROM briefs WHERE run_id=?', (run['id'],))


def test_an_unfinished_draft_file_cannot_cancel_the_live_turn(tmp_path, monkeypatch):
    monkeypatch.setattr('briefloop.interactive_runtime.time.sleep', lambda _: None)
    store, run, source = make_run(tmp_path)
    job = store.enqueue('generate', {'run_id': run['id']})
    harness = FakeHarness()
    runtime = InteractiveRuntime(store, harness)
    folder = store.root / 'jobs' / job['id']
    folder.mkdir(parents=True, exist_ok=True)
    ticks = []

    def publish():
        # What the real callback does: the agent has written a skeleton it still
        # intends to fill in, so the contract does not hold yet.
        ticks.append(len(ticks))
        BriefDraft.model_validate(json.loads((folder / 'draft.json').read_text()))

    (folder / 'draft.json').write_text('{"title":"周报","markdown":""}')

    def tick():
        if runtime.session_id and len(ticks) >= 2:
            harness.finish(runtime.session_id)
        publish()

    result = runtime.execute(job, '任务', folder, tick)
    assert result['returncode'] == 0
    assert harness.cancelled == []
    reasons = [json.loads(row['data'])['error'] for row in
               store.rows("SELECT data FROM events WHERE job_id=? AND kind='draft_admission_deferred'", (job['id'],))]
    assert len(reasons) == 1 and '报告正文不能为空' in reasons[0]


def test_check_draft_reports_drift_in_band_before_the_host_reads_it(tmp_path):
    draft = tmp_path / 'draft.json'
    draft.write_text(json.dumps({'title': '周报', 'markdown': '正文。', 'summary': '自创键'}, ensure_ascii=False))
    done = subprocess.run([sys.executable, '-m', 'briefloop', 'tool', '--workspace', str(tmp_path / 'workspace'),
                           'check-draft', '--file', str(draft)], capture_output=True, text=True, check=True)
    report = json.loads(done.stdout)
    assert report['status'] == 'ok' and report['unknown_fields'] == ['summary']
    draft.write_text(json.dumps({'markdown': '正文。'}, ensure_ascii=False))
    failed = subprocess.run([sys.executable, '-m', 'briefloop', 'tool', '--workspace', str(tmp_path / 'workspace'),
                             'check-draft', '--file', str(draft)], capture_output=True, text=True)
    assert failed.returncode == 1 and json.loads(failed.stdout)['errors'][0]['field'] == 'title'


def test_a_made_up_citation_id_says_which_id_it_was(tmp_path):
    store, run, source = make_run(tmp_path)
    with pytest.raises(ValueError, match='src_invented'):
        store.publish(run['id'], {'title': '周报', 'markdown': '正文。',
                                  'citations': [{'source_id': 'src_invented'}]})


def test_free_form_fields_keep_every_key_they_were_given():
    document = {'type': 'doc', 'content': [], 'editorOnlyExtra': 1}
    cleaned, dropped = prune_unknown({'title': 't', 'markdown': 'x', 'editor_document': document,
                                      'research_notes': [{'anything': True}]}, BriefDraft)
    assert dropped == [] and cleaned['editor_document'] == document
    assert check_artifact({'title': 't', 'markdown': 'x'}, BriefDraft)['status'] == 'ok'
