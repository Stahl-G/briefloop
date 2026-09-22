"""One name per runtime, across the three places that used to keep their own."""
import json
import re
from pathlib import Path

from briefloop.backends import BACKEND_LABELS

ROOT = Path(__file__).resolve().parents[1]
CATALOG = {entry['id']: entry['name']
           for entry in json.loads((ROOT / 'runtime-bridge' / 'catalog.json').read_text(encoding='utf-8'))}


def test_selectable_backends_are_named_the_same_in_python_and_the_catalogue():
    """These drifted before: the catalogue said Codebuddy Code and OpenCode
    while the rest of the product said CodeBuddy Code and Opencode CLI, so the
    discovery card and the settings page named the same host differently."""
    shared = {name for name in BACKEND_LABELS if name in CATALOG}
    assert shared, 'no bridged backend found in the catalogue'
    assert {name: BACKEND_LABELS[name] for name in sorted(shared)} == \
           {name: CATALOG[name] for name in sorted(shared)}


def test_the_card_takes_the_name_from_the_record_it_is_given():
    """The card used to prefer its own table over the name the bridge sent."""
    source = (ROOT / 'frontend' / 'runtime-cards.js').read_text(encoding='utf-8')
    assert 'const brands' not in source
    assert re.search(r'const name\s*=\s*r\.name', source), 'the card must read the name it was given'
    for runtime_id, name in CATALOG.items():
        assert f"'{name}'" not in source, f'{runtime_id} is named in the page as well as the catalogue'


def test_every_backend_python_offers_is_a_runtime_the_bridge_knows():
    from briefloop.backends import BRIDGE_BACKENDS
    assert not set(BRIDGE_BACKENDS) - set(CATALOG)


def test_native_discovery_uses_the_canonical_product_name(monkeypatch):
    from briefloop import native_engine, host_bins
    monkeypatch.setattr(host_bins, 'find', lambda _: '/test/node')
    assert native_engine.discovery()['name'] == BACKEND_LABELS['briefloop-native'] == 'BriefLoop Agent'
