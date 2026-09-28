import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments' / 'jev_semantic'))
from assessment import evaluate, labels_for, review_queue
from dataset import (ReadOnlyStore, assign_splits, digest, export_workspaces, freeze_dataset,
                     json_lines, load_dataset, located_evidence, make_case, report_cases)
from engine import TASKS, execute, outcomes, prefilter, priority, request_body
from review_ui import serve
from briefloop.store import Store


def number_case(paragraph='2027年计划投产10GW。', exposure='public'):
    return make_case('numeric_omission', {'paragraph': paragraph,
        'target': {'text': '2027', 'start': 0, 'end': 4}, 'report': {'objective': '跟踪投产节点'}},
        {'run_id': 'run-one', 'version_id': 'v1', 'sources': [], 'exposure': exposure},
        observed={'verification': 'unbound'}, origin='synthetic')


def freeze_dev(cases, folder):
    # These fixtures test tool behavior, not real heldout samples.
    for i in range(1000):
        seed = f'unit-dev-{i}'
        assign_splits(cases, seed)
        if all(c['split'] == 'dev' for c in cases):
            return freeze_dataset(cases, folder, seed=seed)
    raise AssertionError('No dev seed found for small synthetic fixture')


def frozen(tmp_path, case=None):
    folder = tmp_path / 'dataset'
    freeze_dev([case or number_case()], folder)
    manifest, cases = load_dataset(folder)
    return folder, manifest, cases[0]


def jev_response(choice='fact'):
    keys = TASKS['numeric_omission']['criteria']
    return {'model': 'test-jev-version', 'usage': {'input_tokens': 5},
            'answers': {'judgment': {'type': 'choice', 'choice': choice,
                'probabilities': {k: 1.0 if k == choice else 0.0 for k in keys}}}}


def test_export_keeps_target_headers_and_source_identity_readonly(tmp_path):
    store = Store(tmp_path / 'workspace')
    table = '<table><tr><th>FY2027 revenue USD</th></tr><tr><td>$12 million</td></tr>' + '<tr><td>other</td></tr>' * 1200 + '</table>'
    source = store.add_source('public disclosure', table + '\nRevenue $13 million.\n')
    quote = 'Revenue $12 million.'
    run = store.create_run({'title': 'Report', 'objective': 'Explain'}, [source['id']])
    binding = {'value': 12, 'unit': 'million USD', 'source_id': source['id'], 'locator': 'line 1',
               'source_excerpt': '$12 million', 'report_quote': quote, 'number_text': '$12 million'}
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': quote + '\n2027年客户12家。', 'number_bindings': [binding]})
    before = (store.root / 'briefloop.db').read_bytes()
    readonly = ReadOnlyStore(store.root)
    try:
        first = located_evidence(readonly, binding)
        second = located_evidence(readonly, {**binding, 'locator': 'line 2', 'source_excerpt': '$13 million'})
        assert first['target'] == table and len(first['context']) > 20000
        assert 'FY2027' in first['context'] and '$12 million' in first['context']
        assert second['target'] == 'Revenue $13 million.'
        bad = located_evidence(readonly, {**binding, 'locator': '{"kind":"page","page_index":16}'})
        assert bad['status'] == 'unavailable' and 'target' not in bad
        with pytest.raises(Exception, match='readonly'):
            readonly.db.execute('DELETE FROM briefs')
        cases, _, _ = report_cases(readonly, brief['id'], 'public')
        assert len(cases) == 3
        assert [c['observed']['verification'] for c in cases] == ['checked', 'unbound', 'unbound']
    finally:
        readonly.close()
    assert (store.root / 'briefloop.db').read_bytes() == before
    export_workspaces([(store.root, [brief['id']])], tmp_path / 'export', 'public', 'test-seed')
    assert load_dataset(tmp_path / 'export')[1]


def test_group_split_and_dataset_tampering(tmp_path):
    first, second = number_case(), number_case('2027年预计投产。')
    first['provenance']['sources'] = [{'id': 'one', 'hash': 'shared'}]
    second['provenance'].update(run_id='other-report', sources=[{'id': 'two', 'hash': 'shared'}])
    assign_splits([first, second], 'seed')
    assert first['group_id'] == second['group_id'] and first['split'] == second['split']
    folder, manifest, case = frozen(tmp_path)
    target = folder / (case['case_id'] + '.json')
    target.write_text('{}')
    with pytest.raises(ValueError, match='修改'):
        load_dataset(folder)


def test_requests_preserve_long_inputs_and_do_not_use_labels(tmp_path):
    case = number_case('2027年' + '完整表格' * 20000)
    folder, _, case = frozen(tmp_path, case)
    calls = []
    out = tmp_path / 'run'
    execute(folder, out, 'jev', 'jev-latest', 'https://example.test/evaluate', 'unit-secret', case['split'],
            allow_network=True, max_bytes=1000, transport=lambda *args: calls.append(args))
    assert not calls
    assert outcomes(out)[case['case_id']]['reason'] == 'request_too_large_no_truncation'
    saved = json.loads((out / (case['case_id'] + '.request.json')).read_text())
    assert saved['state'] == case['state']
    assert 'score' not in saved and 'observed' not in saved['state']
    assert 'unit-secret' not in (out / 'events.jsonl').read_text()


def test_failure_retained_resume_and_protocol_freeze(tmp_path, monkeypatch):
    folder, _, case = frozen(tmp_path)
    calls = []
    def transport(*args):
        calls.append(args)
        if len(calls) == 1:
            raise urllib.error.HTTPError('https://example.test/evaluate', 429, 'busy', {}, None)
        return jev_response()
    monkeypatch.setattr('engine.time.sleep', lambda _: None)
    out = tmp_path / 'run'
    kwargs = dict(provider='jev', model='jev-latest', endpoint='https://example.test/evaluate', key='unit-secret',
                  split=case['split'], allow_network=True, transport=transport)
    execute(folder, out, **kwargs)
    events = json_lines(out / 'events.jsonl')
    assert [e['status'] for e in events if e['event'] == 'result'] == ['failed', 'completed']
    execute(folder, out, **kwargs)
    assert len(calls) == 2
    with pytest.raises(ValueError, match='协议已变化'):
        execute(folder, out, **{**kwargs, 'model': 'another-model'})
    outcome = outcomes(out)[case['case_id']]
    assert priority(case, outcome) == '疑似遗漏核验'
    assert len(review_queue(folder, out)) == 1


def test_private_gate_discrete_llm_and_no_false_completion(tmp_path):
    folder, _, case = frozen(tmp_path, number_case(exposure='private'))
    out = tmp_path / 'run'
    calls = []
    execute(folder, out, 'jev', 'jev-latest', 'https://example.test/evaluate', 'unit-secret', case['split'],
            allow_network=True, transport=lambda *args: calls.append(args))
    assert not calls and outcomes(out)[case['case_id']]['status'] == 'skipped'
    assert '未完成' in priority(case, outcomes(out)[case['case_id']])
    body = request_body(case, 'llm', 'ordinary-model')
    assert body['response_format']['json_schema']['schema']['required'] == ['choice']
    case['safety']['must_review'] = True
    assert '必须复核' in priority(case, {'status': 'completed', 'answer': {'choice': 'incidental'}})


def test_real_source_change_export_preserves_proposals_and_never_writes_back(tmp_path):
    from briefloop.evidence import bind_claim, create_claim, create_span
    from briefloop.source_updates import record_change
    store = Store(tmp_path / 'workspace')
    old = store.add_source('Old', '计划2027年投产10GW。')
    new = store.add_source('New', '计划调整为2028年投产10GW。')
    run = store.create_run({'title': '项目周报', 'objective': '投产时间是否变化？'}, [old['id'], new['id']])
    brief = store.publish(run['id'], {'title': '项目周报', 'editor_document': {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '计划2027年投产10GW。'}]}]}})
    span = create_span(store, {'source_id': old['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    claim = create_claim(store, run['id'], {'kind': 'fact', 'statement': '计划2027年投产10GW。',
        'supports': [{'span_id': span['id'], 'supports_quote': '计划2027年投产10GW。'}]})
    document = json.loads(store.one('briefs', brief['id'])['editor_document'])
    bind_claim(store, brief['id'], claim['id'], document['content'][0]['attrs']['blockId'], '计划2027年投产10GW。')
    record_change(store, {'old_source_id': old['id'], 'new_source_id': new['id'], 'kind': 'update',
        'relation': 'unknown', 'description': '投产时间变化', 'scope': '项目投产时间', 'information_cutoff': '2026-09-17'}, run_id=run['id'])
    before = store.one('briefs', brief['id'])
    export_workspaces([(store.root, [brief['id']])], tmp_path / 'dataset', 'public', 'seed')
    _, cases = load_dataset(tmp_path / 'dataset')
    assert {c['task'] for c in cases} == {'numeric_omission', 'change_materiality', 'conclusion_update'}
    update = next(c for c in cases if c['task'] == 'conclusion_update')
    assert update['state']['old_conclusion']['statement'] == '计划2027年投产10GW。'
    assert update['state']['new_source']['text'] == '计划调整为2028年投产10GW。'
    assert store.one('briefs', brief['id']) == before and not store.rows('SELECT * FROM feedback')


def test_prefilter_boundaries(tmp_path):
    samples = [('参见《2024年年度报告》第12页。', '2024', True),
               ('《2030年行动方案》将目标期改为2030年。', '2030', False),
               ('必须用v2才支持这项能力。', '2', False),
               ('产品编号123是本次准入要求。', '123', False),
               ('参见《2024年报告》。', '2024', True),
               ('《2023年报》指出项目将在2027年投产，另见《项目说明》。', '2027', False),
               ('甲公司计划于2024年形成10GW产能，该计划参见《2026年年度报告》第12页。', '2024', False),
               ('形成10GW产能，该计划参见《2026年年度报告》第12页。', '2026', True),
               ('参见原披露第23页，收入同比增长8%。', '23', True),
               ('公司市占率为18.6%，位列第3。', '3', False),
               ('本节采用口径 v2 编制。', '2', False),
               ('2024年年报显示收入增长8%。', '2024', False),
               ('明细见附表第12行。', '12', True),
               ('产能结构详见图4。', '4', True),
               ('1. 经营摘要', '1', True),
               ('参见2024年年报第7页。', '2024', True)]
    for paragraph, text, filtered in samples:
        start = paragraph.index(text)
        case = number_case(paragraph)
        case['state']['target'] = {'text': text, 'start': start, 'end': start + len(text)}
        assert (prefilter(case) is not None) == filtered, paragraph
        assert prefilter(case) is None or prefilter(case)['choice'] == 'incidental'


def test_prefilter_skips_model_and_freezes_protocol(tmp_path):
    paragraph = '该计划参见《2026年年度报告》第12页。'
    start = paragraph.index('2026')
    case = number_case(paragraph)
    case['state']['target'] = {'text': '2026', 'start': start, 'end': start + 4}
    folder, _, case = frozen(tmp_path, case)
    calls = []
    out = tmp_path / 'run'
    execute(folder, out, 'jev', 'jev-latest', 'https://example.test/evaluate', 'unit-secret', case['split'],
            allow_network=True, use_prefilter=True, transport=lambda *args: calls.append(args))
    assert not calls
    outcome = outcomes(out)[case['case_id']]
    assert outcome['via'] == 'code_prefilter' and outcome['answer']['choice'] == 'incidental'
    with pytest.raises(ValueError, match='协议已变化'):
        execute(folder, out, 'jev', 'jev-latest', 'https://example.test/evaluate', 'unit-secret', case['split'],
                allow_network=True, transport=lambda *args: calls.append(args))


def test_llm_probability_variant(tmp_path):
    case = number_case()
    plain = request_body(case, 'llm', 'm')
    assert plain['response_format']['json_schema']['schema']['required'] == ['choice']
    with_probs = request_body(case, 'llm', 'm', with_probabilities=True)
    assert with_probs['response_format']['json_schema']['schema']['required'] == ['choice', 'probabilities']
    folder, _, case = frozen(tmp_path, case)
    keys = TASKS['numeric_omission']['criteria']
    response = {'model': 'm-1', 'choices': [{'message': {'content': json.dumps(
        {'choice': 'fact', 'probabilities': {k: 0.9 if k == 'fact' else 0.05 for k in keys}})}}]}
    out = tmp_path / 'run'
    execute(folder, out, 'llm', 'm', 'https://example.test/chat', 'unit-secret', case['split'],
            allow_network=True, with_probabilities=True, transport=lambda *args: response)
    assert outcomes(out)[case['case_id']]['answer']['probabilities']['fact'] == 0.9
    with pytest.raises(ValueError, match='概率变体'):
        execute(folder, tmp_path / 'bad', 'jev', 'jev-latest', 'https://example.test/e', 'k',
                case['split'], allow_network=True, with_probabilities=True)


def test_construction_scoring_threshold_and_source_discipline(tmp_path):
    cases = [number_case('甲公司收入12.4亿元。'), number_case('参见《2024年年度报告》第12页。')]
    folder = tmp_path / 'dataset'
    freeze_dev(cases, folder)
    manifest, loaded = load_dataset(folder)
    cite = next(c for c in loaded if '《' in c['state']['paragraph'])
    fact = next(c for c in loaded if '《' not in c['state']['paragraph'])
    assert cite['split'] == fact['split']
    out = tmp_path / 'run'
    execute(folder, out, 'jev', 'jev-latest', 'https://example.test/e', 'k', cite['split'],
            allow_network=True, transport=lambda *args: jev_response(
                'incidental' if '《' in args[1]['state']['paragraph'] else 'fact'))
    truth = tmp_path / 'truth.jsonl'
    with truth.open('w') as stream:
        for case, label in ((cite, 'incidental'), (fact, 'fact')):
            stream.write(json.dumps({'kind': 'label', 'source': 'construction', 'case_id': case['case_id'],
                'dataset_id': manifest['dataset_id'], 'truth': label}) + '\n')
        stream.write(json.dumps({'kind': 'label', 'source': 'human', 'case_id': fact['case_id'],
            'dataset_id': manifest['dataset_id'], 'truth': 'incidental'}) + '\n')
    from assessment import score_construction
    scored = score_construction(folder, out, truth)
    assert scored['tasks'][0]['classification']['n'] == 2
    assert scored['tasks'][0]['classification']['false_positive_count'] == 0
    with pytest.raises(ValueError, match='身份不符'):
        bad = tmp_path / 'bad.jsonl'
        bad.write_text(json.dumps({'kind': 'label', 'source': 'construction',
            'case_id': fact['case_id'], 'dataset_id': 'x' * 64, 'truth': 'fact'}) + '\n')
        score_construction(folder, out, bad)


def test_annotation_http_loop_and_unlabelled_evaluation(tmp_path):
    folder, manifest, case = frozen(tmp_path, number_case('2027年<script>alert(1)</script>'))
    out = tmp_path / 'rules'
    execute(folder, out, split=case['split'])
    annotations = tmp_path / 'labels.jsonl'
    summary = evaluate(folder, [out], annotations)
    assert summary['comparisons'][0]['tasks'][0]['classification']['n'] == 0
    server = serve(folder, annotations)
    worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        text = urllib.request.urlopen(url).read().decode()
        assert '<script>alert' not in text and '&lt;script&gt;' in text
        import re
        fields = {name: re.search(r'name="' + name + r'" value="([^"]+)"', text)[1] for name in ('csrf', 'visit', 'case_id')}
        fields.update(reviewer='test-reviewer', choice='fact', reason='合成测试：年份表示计划时间')
        req = urllib.request.Request(url + '/record', data=urllib.parse.urlencode(fields).encode(),
                                     headers={'Origin': url})
        assert urllib.request.urlopen(req).status == 200
        labels = labels_for(folder, annotations)
        assert labels[case['case_id']]['choice'] == 'fact'
        assert labels[case['case_id']]['production_writeback'] is False
        summary = evaluate(folder, [out], annotations)
        assert summary['comparisons'][0]['tasks'][0]['classification']['n'] == 1
        assert summary['human_time_savings'] is None
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(urllib.request.Request(url + '/record', data=b'a=b', headers={'Origin': 'https://evil.test'}))
        assert exc.value.code == 403
    finally:
        server.shutdown();worker.join();server.server_close()
    server = serve(folder, annotations, mode='review', run_folder=out)
    worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        text = urllib.request.urlopen(url).read().decode()
        fields = {name: re.search(r'name="' + name + r'" value="([^"]+)"', text)[1] for name in ('csrf', 'visit', 'case_id')}
        fields.update(reviewer='test-reviewer', choice='needs_material', reason='需补充原始披露')
        request = urllib.request.Request(url + '/record', data=urllib.parse.urlencode(fields).encode(), headers={'Origin': url})
        assert urllib.request.urlopen(request).status == 200
        queue = review_queue(folder, out, annotations)
        assert queue[0]['action_status'] == 'needs_material' and queue[0]['affects_release'] is False
        assert labels_for(folder, annotations)[case['case_id']]['choice'] == 'fact'
    finally:
        server.shutdown();worker.join();server.server_close()


def test_dependency_sources_keep_numberless_reports_in_one_split(tmp_path):
    from briefloop.evidence import bind_claim, create_claim, create_span
    from briefloop.source_updates import record_change
    store = Store(tmp_path / 'workspace')
    shared = store.add_source('共同政策', '只有完成验收才可执行。')
    shared_span = create_span(store, {'source_id': shared['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    versions = []
    for label in ('甲', '乙'):
        old = store.add_source(label + '旧', label + '项目具备执行条件。')
        new = store.add_source(label + '新', label + '项目因场地问题推迟。')
        run = store.create_run({'title': label + '项目', 'objective': '核对执行条件'}, [old['id'], new['id'], shared['id']])
        text = label + '项目按共同政策具备执行条件。'
        brief = store.publish(run['id'], {'title': label + '项目', 'editor_document': {'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}]}})
        span = create_span(store, {'source_id': old['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
        request = {'kind': 'fact', 'statement': text, 'supports': [{'span_id': span['id'], 'supports_quote': text}]}
        if label == '甲':
            request['supports'].append({'span_id': shared_span['id'], 'supports_quote': text})
        else:
            premise = create_claim(store, run['id'], {'kind': 'fact', 'statement': '只有完成验收才可执行。',
                'supports': [{'span_id': shared_span['id'], 'supports_quote': '只有完成验收才可执行。'}]})
            request.update(kind='inference', premise_claim_ids=[premise['id']], reasoning='合成测试：演示前提来源分组，不评价真实支持关系')
        claim = create_claim(store, run['id'], request)
        document = json.loads(store.one('briefs', brief['id'])['editor_document'])
        bind_claim(store, brief['id'], claim['id'], document['content'][0]['attrs']['blockId'], text)
        record_change(store, {'old_source_id': old['id'], 'new_source_id': new['id'], 'kind': 'update',
            'relation': 'unknown', 'description': '执行条件变化', 'scope': '项目执行条件',
            'information_cutoff': '2026-09-20'}, run_id=run['id'])
        versions.append(brief['id'])
    out = tmp_path / 'dataset'
    export_workspaces([(store.root, versions)], out, 'public', 'offline-review', origin='synthetic')
    _, cases = load_dataset(out)
    assert not [c for c in cases if c['task'] == 'numeric_omission']
    conclusions = [c for c in cases if c['task'] == 'conclusion_update']
    assert len(conclusions) == 2
    assert all(shared['hash'] in {s['hash'] for s in c['provenance']['sources']} for c in conclusions)
    assert len({c['group_id'] for c in conclusions}) == len({c['split'] for c in conclusions}) == 1
