"""One name per task kind, checked where the names could drift apart again."""
import re
from pathlib import Path

from briefloop.task_labels import LABELS, REPORTED, reported_labels

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src' / 'briefloop'


def test_every_surface_reads_the_shared_table():
    """No module may keep its own kind->name map.

    Each of these used to: the status note, the progress card and the session
    title, so one task had up to four names depending on where you saw it.
    """
    owners = {}
    for path in sorted(SOURCE.rglob('*.py')):
        text = path.read_text(encoding='utf-8')
        for kind, name in LABELS.items():
            # A literal name next to its own kind is a table, not a mention.
            if re.search(r"'" + kind + r"'\s*:\s*'" + re.escape(name), text):
                owners.setdefault(name, []).append(path.name)
    assert owners == {name: ['task_labels.py'] for name in LABELS.values()}, owners


def test_the_page_is_given_the_names_and_keeps_none(tmp_path):
    from briefloop.store import Store
    state = Store(tmp_path).snapshot()
    assert state['task_labels'] == reported_labels()
    assert set(state['task_labels']) == set(REPORTED)
    # A name written next to its kind is a table; the same words in prose are not.
    tables = []
    for path in sorted((ROOT / 'frontend').glob('*.js')):
        page = path.read_text(encoding='utf-8')
        for kind, name in LABELS.items():
            if re.search(r"\b" + kind + r"\s*:\s*'" + re.escape(name), page):
                tables.append(f'{path.name}: {kind}:{name}')
    assert tables == [], f'the page keeps its own table: {tables}'


def test_steps_are_named_but_are_not_reported_as_tasks():
    """company_review is a stage inside generation, not a task of its own."""
    assert LABELS['company_review'] and 'company_review' not in REPORTED
    assert 'company_review' not in reported_labels()


def test_naming_an_internal_step_does_not_make_it_a_user_task(monkeypatch):
    monkeypatch.setitem(LABELS, 'internal_probe', '内部探测')
    assert 'internal_probe' not in reported_labels()
