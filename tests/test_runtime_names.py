"""One name per runtime, across the three places that used to keep their own."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = {entry['id']: entry['name']
           for entry in json.loads((ROOT / 'runtime-bridge' / 'catalog.json').read_text(encoding='utf-8'))}


def test_every_backend_python_offers_is_a_runtime_the_bridge_knows():
    from briefloop.backends import BRIDGE_BACKENDS
    assert not set(BRIDGE_BACKENDS) - set(CATALOG)
