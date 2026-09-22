"""Phase-1 acceptance: run a real restricted review through the native engine.

Copies a real workspace (never touches the original), enqueues a real 'review'
job pinned to agent_backend=briefloop-native, and drives run_review end to end:
packet build -> engine session -> model reads packet via packet tools only ->
ReviewOutput JSON -> accept_review.

Usage:
  OPENCODE_API_KEY=... python accept_review.py <workspace> <version_id> [model] [--backend briefloop-native|opencode]

The same frozen version can be reviewed through either backend, which is the
controlled A/B: identical packet, identical model, different runtime. Prints
the review id, status, finding counts and the job folder for audit.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from briefloop.store import Store  # noqa: E402
from briefloop.interactive_runtime import InteractiveRuntime  # noqa: E402
from briefloop.native_engine import NativeEngine  # noqa: E402
from briefloop.native_harness import NativeHarness  # noqa: E402
from briefloop.opencode_harness import OpencodeHarness  # noqa: E402
from briefloop.review import run_review, get_review  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    backend = next((a.split('=', 1)[1] for a in sys.argv[1:] if a.startswith('--backend=')),
                   'briefloop-native')
    source = Path(args[0]).resolve()
    version_id = args[1]
    model = args[2] if len(args) > 2 else 'opencode-go/deepseek-v4.1-flash'
    work = Path(tempfile.mkdtemp(prefix='bl-native-accept-')).resolve()
    shutil.copytree(source, work / 'ws')
    store = Store(work / 'ws')
    engine = NativeEngine()
    harness = NativeHarness(store, engine)
    opencode = OpencodeHarness(store)
    runtime = InteractiveRuntime(store, backends={
        'briefloop-native': harness, 'opencode': opencode})
    try:
        job = store.enqueue('review', {
            'version_id': version_id,
            'agent_backend': backend,
            'role_models': {'evaluator': {'model': model, 'model_variant': 'low'}},
        })
        folder = work / 'ws' / 'jobs' / job['id']
        print(f'job={job["id"]} backend={backend} model={model} workspace_copy={work}')
        t0 = time.monotonic()
        result = run_review(store, runtime, job, version_id, folder)
        seconds = time.monotonic() - t0
        review = get_review(store, result['id'] if isinstance(result, dict) else result)
        data = review['result'] or {}
        print(f'--- ACCEPTED in {seconds:.1f}s ---')
        print(json.dumps({
            'review_id': review['id'],
            'status': review['status'],
            'review_status': data.get('status'),
            'findings': len(data.get('findings', [])),
            'claim_checks': len(data.get('claim_checks', [])),
            'response_checks': len(data.get('response_checks', [])),
            'unchecked_items': len(data.get('unchecked_items', [])),
            'overall': (data.get('assessment') or {}).get('overall'),
            'job_folder': str(folder),
        }, ensure_ascii=False, indent=2))
    finally:
        harness.close()
        try:
            opencode.close()
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
