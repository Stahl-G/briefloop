"""A finished draft must survive schema drift; only the offending key is lost."""
import json
import subprocess
import sys
from briefloop.store import Store


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
        (folder / 'draft.json').write_text(json.dumps(self.draft, ensure_ascii=False),encoding='utf-8')
        on_tick()
        return {}


def test_check_draft_reports_drift_in_band_before_the_host_reads_it(tmp_path):
    draft = tmp_path / 'draft.json'
    draft.write_text(json.dumps({'title': '周报', 'markdown': '正文。', 'summary': '自创键'}, ensure_ascii=False),encoding='utf-8')
    done = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'briefloop', 'tool', '--workspace', str(tmp_path / 'workspace'),
                           'check-draft', '--file', str(draft)], capture_output=True, encoding='utf-8', check=True)
    report = json.loads(done.stdout)
    assert report['status'] == 'ok' and report['unknown_fields'] == ['summary']
    draft.write_text(json.dumps({'markdown': '正文。'}, ensure_ascii=False),encoding='utf-8')
    failed = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'briefloop', 'tool', '--workspace', str(tmp_path / 'workspace'),
                             'check-draft', '--file', str(draft)], capture_output=True, encoding='utf-8')
    assert failed.returncode == 1 and json.loads(failed.stdout)['errors'][0]['field'] == 'title'


def test_check_draft_run_returns_advisory_diagnostics_without_publishing(tmp_path):
    store, run, source = make_run(tmp_path)
    path = tmp_path/'short.json'
    path.write_text(json.dumps({'title': '短稿', 'markdown': '收入增长20%。[@'+source['id']+']'},ensure_ascii=False))
    before = store.rows('SELECT id,hash FROM briefs')
    done = subprocess.run([sys.executable, '-m', 'briefloop', 'tool', '--workspace', str(store.root),
                           'check-draft', '--run', run['id'], '--file', str(path)],
                          capture_output=True, encoding='utf-8', check=True)
    diagnostics = json.loads(done.stdout)['diagnostics']
    assert diagnostics['length']['below_target']
    assert diagnostics['citations']['missing_locator'] == [source['id']]
    assert diagnostics['review_status'] == 'not_reviewed'
    assert store.rows('SELECT id,hash FROM briefs') == before
