"""English report bodies: language reaches writers and reviewers, exports and layouts.

The interface and internal records stay Chinese; only report-facing text changes.
"""
from io import BytesIO
import json
import threading
from zipfile import ZipFile

import pytest

from briefloop.deliverable_spec import instructions, reader_contract_schema, resolve, save_reader_contract
from briefloop.delivery_checks import quantities
from briefloop.document_model import markdown_document
from briefloop.export_jobs import enqueue_export, generate_word, output_path
from briefloop.exports import docx_bytes, reader_markdown
from briefloop.models import Requirements
from briefloop.store import Store, dump
from briefloop.templates import import_builtin


def test_language_is_an_enum_and_english_lengths_count_words():
    assert Requirements(title='t', objective='o').language == 'zh'
    for value, expected in [('中文', 'zh'), ('zh-CN', 'zh'), ('English', 'en'), ('en-US', 'en'), ('英文', 'en')]:
        assert Requirements(title='t', objective='o', language=value).language == expected
    with pytest.raises(ValueError):
        Requirements(title='t', objective='o', language='日本語')
    english = Requirements(title='t', objective='o', language='en')
    assert (english.target_words, english.max_words) == (1000, 1300)
    assert (Requirements(title='t', objective='o', language='en', research_tier='deep').target_words) == 6500
    # An explicit number is the user's choice in either language.
    assert Requirements(title='t', objective='o', language='en', target_words=1500, max_words=2000).target_words == 1500


def test_writers_and_reviewers_see_english_while_chinese_specs_stay_unchanged():
    base = {'title': 't', 'objective': 'o', 'target_words': 1000, 'max_words': 1300}
    chinese = resolve({**base, 'language': 'zh'})
    assert 'language' not in chinese and chinese == resolve({**base, 'language': '中文'}) == resolve(base)
    english = resolve({**base, 'language': 'en'})
    assert english['language'] == 'en'
    # Language is frozen per run: a legacy "English" run keeps its saved contract.
    assert reader_contract_schema(english) == reader_contract_schema(resolve({**base, 'language': 'English'}))
    assert reader_contract_schema(english) == reader_contract_schema(chinese)
    assert '本轮报告正文语言：英文' in instructions(english, role='analyst')
    assert '本轮报告正文语言：英文' in instructions(english, role='revision')
    assert '发现与理由仍用中文写' in instructions(english, role='reviewer')
    assert 'excerpt 保持原文逐字' in instructions(english, role='scout')
    assert '本轮报告正文语言' not in instructions(chinese, role='analyst')


def test_legacy_english_run_accepts_contract_built_from_normalized_requirements(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = store.add_source('local', 'text')
    run = store.create_run({'title': 't', 'objective': 'o'}, [source['id']])
    legacy = {**json.loads(run['requirements']), 'language': 'English'}
    with store.tx() as connection:
        connection.execute('UPDATE runs SET requirements=? WHERE id=?', (dump(legacy), run['id']))
    # The generation packet validates (normalizes) requirements before building the schema.
    spec = resolve(Requirements.model_validate(legacy).model_dump())
    fingerprint = reader_contract_schema(spec)['properties']['source_fingerprint']['const']
    item = spec['requirement_items'][0]
    contract = {'source_fingerprint': fingerprint, 'clauses': [
        {'requirement_id': item['requirement_id'], 'kind': 'reader_content', 'source_quote': 'o', 'instruction': 'answer'}]}
    assert save_reader_contract(store, run['id'], contract)['source_fingerprint'] == fingerprint


def test_english_numbers_bind_to_chinese_source_units():
    found = {text: value for text, value in
             ((m, v) for m in ['RMB 12.3 billion', '123亿元', 'US$1.2 billion', '1.2万亿元', '$1.2 trillion',
                               '3.5 percentage points', '40 pp'] for _, _, v in quantities(m))}
    assert found['RMB 12.3 billion'] == found['123亿元']
    assert found['US$1.2 billion'][1] == 'USD'
    assert found['1.2万亿元'][0] == found['$1.2 trillion'][0]
    assert found['3.5 percentage points'][1] == found['40 pp'][1] == 'percentage_point'


def test_english_word_exports_label_sources_and_use_english_layouts(tmp_path):
    store = Store(tmp_path / 'workspace')
    import_builtin(store)
    layout = next(row for row in store.snapshot()['templates'] if row['name'] == '英文研报·极简蓝')
    assert layout['language_hint'] == 'en' and layout['workflow_hint'] == 'stock_research'
    assert next(row for row in store.snapshot()['templates'] if row['name'] == '券商研报·极简蓝')['language_hint'] == 'zh'
    source = store.add_source('Annual report', 'Revenue was RMB 12.3 billion.', url='https://example.test/ar')
    run = store.create_run({'title': 'Acme update', 'objective': 'Explain results', 'language': 'en',
                            'template_id': layout['id']}, [source['id']])
    assert json.loads(run['requirements'])['language'] == 'en'
    markdown = '## Key Takeaways\n\nRevenue reached RMB 12.3 billion [@' + source['id'] + '].'
    brief = store.publish(run['id'], {'title': 'Acme update', 'editor_document': markdown_document(markdown),
                                      'citations': [{'source_id': source['id'], 'locator': 'line 1'}]})
    assert '## Sources' in reader_markdown(store, brief)
    job = enqueue_export(store, brief['id'])
    generate_word(store, job, threading.Event())
    with ZipFile(output_path(store, job)) as archive:
        body = archive.read('word/document.xml').decode()
        footers = ''.join(archive.read(n).decode() for n in archive.namelist() if n.startswith('word/footer'))
    assert '>Sources<' in body and '来源' not in body
    assert 'Page ' in footers and '第 ' not in footers
    assert 'BLHeadingviews' in body.replace(' ', ''), 'English chapter heading must take the layout style'
    # The general layout (no template) labels its source list the same way.
    generic = docx_bytes(document=markdown_document(markdown), source_records={source['id']: source},
                         citations=[{'source_id': source['id'], 'locator': 'line 1'}], language='en')
    with ZipFile(BytesIO(generic)) as archive:
        assert '>Sources<' in archive.read('word/document.xml').decode()
