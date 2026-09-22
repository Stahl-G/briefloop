"""Fixed review/release JSON is UTF-8 even in a non-UTF-8 API host."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from briefloop.store import Store, dump


TEXT = '合成修订记录 🧭 café'


def _review_case(root):
    from briefloop.review import accept_review, respond
    from test_review_learning import save_review

    store = Store(root)
    source = store.add_source(TEXT, 'Revenue was 12 million USD. ' + TEXT)
    run = store.create_run({'title': TEXT, 'objective': 'Explain revenue'}, [source['id']])
    before = store.publish(run['id'], {'title': TEXT, 'markdown': 'Revenue 120 million USD. ' + TEXT})
    first = save_review(store, before, 'review_before')
    first['findings'] = [{'kind': 'insufficient_evidence', 'severity': 'major',
                          'description': TEXT, 'evidence': 'Source states 12, not 120.'}]
    accept_review(store, 'review_before', first)
    assert len(store.rows('SELECT id FROM assessments')) == 1
    assert not store.rows('SELECT id FROM feedback')

    after = store.publish(run['id'], {'title': TEXT, 'markdown': 'Revenue 12 million USD. ' + TEXT},
                          parent_id=before['id'])
    finding = store.rows('SELECT id FROM review_findings')[0]
    response = respond(store, finding['id'], after['id'], 'corrected', TEXT)
    job = store.enqueue('revise', {'version_id': after['id']})
    store.event(job['id'], 'revision_progress', {'message': TEXT})
    final = save_review(store, after, 'review_after')
    final['response_checks'] = [{'response_id': response['id'], 'decision': 'resolved', 'reason': TEXT}]
    accept_review(store, 'review_after', final)
    rows = store.rows("SELECT * FROM feedback WHERE kind='review_correction'")
    assert len(rows) == 1
    feedback = rows[0]
    data = json.loads(feedback['data'])
    assert TEXT in data['before_text'] and TEXT in data['after_text']
    assert TEXT in dump(data['response']) and TEXT in dump(data['execution_records'])
    assessments = store.rows('SELECT * FROM assessments ORDER BY id')
    assert len(assessments) == 2

    # Simulate a crash after Review/assessment commit but before feedback commit.
    # The same accepted output must recover exactly once without another model.
    with store.tx() as connection:
        connection.execute('DELETE FROM feedback WHERE id=?', (feedback['id'],))
    accept_review(store, 'review_after', final)
    accept_review(store, 'review_after', final)
    restored = store.rows("SELECT * FROM feedback WHERE kind='review_correction'")
    assert len(restored) == 1 and restored[0]['id'] == feedback['id']
    assert restored[0]['data'] == feedback['data']
    assert store.rows('SELECT * FROM assessments ORDER BY id') == assessments
    assert len(store.rows('SELECT id FROM reviews')) == 2


def _release_case(root):
    # The existing fixture labels its transport and judgments synthetic; no model.
    from briefloop import backends
    from briefloop.release import release_file, validate_release
    from test_release import reviewed_report, complete_release

    backends.CAPABILITIES['codex'] |= {'restricted_review'}
    store, _, brief, _ = reviewed_report(root)
    release, job = complete_release(store, brief)
    assert store.one('jobs', job['id'])['status'] == 'complete'
    assert validate_release(store, release)['version_id'] == brief['id']
    original = release_file(store, release['id']).read_bytes()
    assert release_file(store, release['id']).read_bytes() == original
    records = store.root / release['result']['manifest_path']
    assert '达到要求' in (records.parent / 'records.json').read_text(encoding='utf-8')


def _manifest_case(root):
    from briefloop import backends
    from briefloop.release import sha, validate_release
    from test_release import reviewed_report, complete_release

    backends.CAPABILITIES['codex'] |= {'restricted_review'}
    store, _, brief, _ = reviewed_report(root)
    release, _ = complete_release(store, brief)
    original_path = store.root / release['result']['manifest_path']
    manifest = json.loads(original_path.read_text(encoding='utf-8'))
    # Current generated identifiers are ASCII. A valid JSON extension must still
    # obey the fixed UTF-8 protocol, independently of the host's locale.
    manifest['producer_note'] = TEXT
    path = original_path.with_name('unicode-manifest.json')
    path.write_bytes(dump(manifest).encode('utf-8'))
    extended = deepcopy(release)
    extended['result'].update(manifest_path=str(path.relative_to(store.root)),
                              manifest_hash=sha(path.read_bytes()))
    assert validate_release(store, extended)['producer_note'] == TEXT

    # Hash-valid bytes are not permission to reinterpret malformed UTF-8 as GBK.
    # A1 A1 is a valid GBK ideographic space, but is invalid standalone UTF-8.
    broken = dump({**manifest, 'producer_note': 'BAD_UTF8'}).encode('utf-8').replace(b'BAD_UTF8', b'\xa1\xa1')
    path.write_bytes(broken)
    extended['result']['manifest_hash'] = sha(broken)
    with pytest.raises(UnicodeDecodeError):
        validate_release(store, extended)


@pytest.mark.parametrize('case', ['review', 'release', 'manifest'])
def test_fixed_packets_in_non_utf8_subprocess(tmp_path, case):
    import briefloop

    # Use whichever package pytest imported, including an installed-wheel run.
    paths = [str(Path(briefloop.__file__).resolve().parent.parent), str(Path(__file__).parent)]
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(paths), 'PYTHONUTF8': '0',
           'PYTHONCOERCECLOCALE': '0', 'LC_ALL': 'C', 'PYTHONIOENCODING': 'utf-8'}
    code = '''import json, locale, sys
from pathlib import Path
import test_review_packet_utf8 as cases
assert sys.flags.utf8_mode == 0
encoding = locale.getpreferredencoding(False)
if encoding.lower().replace('-', '') == 'utf8':
    print(json.dumps({'skip': 'host locale is already UTF-8'}))
else:
    getattr(cases, '_' + sys.argv[1] + '_case')(Path(sys.argv[2]))
    print(json.dumps({'case': sys.argv[1], 'encoding': encoding, 'utf8_mode': 0}))
'''
    result = subprocess.run([sys.executable, '-X', 'utf8=0', '-c', code, case, str(tmp_path)],
                            env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    if outcome.get('skip'):
        pytest.skip(outcome['skip'])
    assert outcome['case'] == case and outcome['utf8_mode'] == 0
