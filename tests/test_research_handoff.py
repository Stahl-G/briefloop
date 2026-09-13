"""Deep-research handoff contract: schema validation, prompt carry-over, budget-exhaustion ending."""
import json
import threading
import pytest
from briefloop import duckduckgo, research_budget, research_plan, websearch
from briefloop.scout_tools import HandoffError, check_handoff
from briefloop.store import Store


def deep_run(tmp_path, *, values=None):
    store = Store(tmp_path)
    req = {'title': 'Deep report', 'objective': 'o', 'allow_web': True}
    if values:
        req['research_budget'] = values
    return store, store.create_run(req, [], research_protocol='quality_v1')


def ddg_html(urls):
    from urllib.parse import quote
    rows = []
    for index, url in enumerate(urls):
        redirect = '//duckduckgo.com/l/?uddg=' + quote(url, safe='') + '&amp;rut=h' + str(index)
        rows.append(f'<div class="result"><h2 class="result__title"><a class="result__a" href="{redirect}">Title {index}</a></h2>'
                    f'<a class="result__snippet" href="{redirect}">Snippet {index}</a></div>')
    html = '<html><body>' + ''.join(rows) + '</body></html>'
    return html, html.encode()


def test_check_handoff_rejects_bare_urls_and_marks_uncited_learnings(tmp_path):
    store, run = deep_run(tmp_path)
    source = store.add_source('Disclosure', 'Revenue 12 million USD in H1.')
    store.attach_source(run['id'], source['id'])
    accepted = check_handoff(store, run['id'], {
        'learnings': [{'summary': '上半年收入 1200 万美元', 'source_id': source['id'], 'locator': 'line 1'},
                      {'summary': '对手口径尚未核实'}],
        'follow_ups': ['对手 H1 收入'], 'covered': ['本期收入'], 'open_questions': ['市场份额']})
    assert accepted['learnings'][0] == {'summary': '上半年收入 1200 万美元', 'source_id': source['id'],
                                        'locator': 'line 1', 'status': '已引用'}
    assert accepted['learnings'][1] == {'summary': '对手口径尚未核实', 'status': '待证'}
    assert accepted['unverified'] == 1
    with pytest.raises(HandoffError) as error:
        check_handoff(store, run['id'], {'learnings': [
            {'summary': '把 URL 当来源', 'source_id': 'https://example.test/report', 'locator': 'line 1'},
            {'summary': '多余 url 字段', 'url': 'https://example.test/other'},
            {'summary': '来源未登记', 'source_id': 'src_missing', 'locator': 'line 2'},
            {'summary': '缺 locator', 'source_id': source['id']},
            'not an object']})
    violations = {item['path']: item for item in error.value.errors}
    assert set(violations) == {'learnings[0]', 'learnings[1]', 'learnings[2].source_id', 'learnings[3]', 'learnings[4]'}
    assert [violations[path]['code'] for path in ('learnings[0]', 'learnings[1]')] == ['bare_url', 'bare_url']
    assert all('裸 URL' in item['message'] and 'add-url' in item['message']
               for item in error.value.errors if item['code'] == 'bare_url')
    assert violations['learnings[2].source_id']['code'] == 'unknown_source'
    assert violations['learnings[3]']['code'] == 'citation_incomplete'


def test_check_handoff_requires_parseable_locators(tmp_path):
    """A non-empty locator is not enough: the next round must be able to read it."""
    store, run = deep_run(tmp_path)
    source = store.add_source('Disclosure', 'Revenue 12 million USD in H1.')
    store.attach_source(run['id'], source['id'])
    accepted = check_handoff(store, run['id'], {'learnings': [
        {'summary': '文本行', 'source_id': source['id'], 'locator': 'line 2-5'},
        {'summary': '页码', 'source_id': source['id'], 'locator': 'Page 3'},
        {'summary': '结构化定位', 'source_id': source['id'],
         'locator': '{"kind":"pdf","page":2}'}]})
    assert all(item['status'] == '已引用' for item in accepted['learnings'])
    with pytest.raises(HandoffError) as error:
        check_handoff(store, run['id'], {'learnings': [
            {'summary': '叙述式定位', 'source_id': source['id'], 'locator': '见正文第三段附近'},
            {'summary': 'URL 当定位', 'source_id': source['id'], 'locator': 'https://example.test/x#p3'},
            {'summary': '行号倒置', 'source_id': source['id'], 'locator': 'line 5-2'},
            {'summary': '坏 JSON', 'source_id': source['id'], 'locator': '{"kind":'},
            {'summary': 'JSON 无 kind', 'source_id': source['id'], 'locator': '{"page":2}'}]})
    violations = {item['path']: item for item in error.value.errors}
    assert set(violations) == {f'learnings[{i}].locator' for i in range(5)}
    assert all(item['code'] == 'locator_unparseable' for item in violations.values())
    assert '不可解析' in violations['learnings[0].locator']['message']


def test_deep_generation_prompt_carries_previous_handoff_and_remaining_budget(tmp_path):
    from briefloop.runtime import generation_prompt
    store, run = deep_run(tmp_path, values={'search_requests': 4, 'candidate_urls': 10, 'source_pages': 4})
    store.set_meta('settings', {**store.settings(), 'search_provider': 'duckduckgo'})  # managed provider: remaining is a number
    research_plan.freeze(store, run['id'], preset='deep', structure={'breadth': 2, 'depth': 2})
    source = store.add_source('Disclosure', 'Revenue 12 million USD in H1.')
    store.attach_source(run['id'], source['id'])
    folder = store.root / 'jobs' / 'probe'
    folder.mkdir(parents=True)
    generation_prompt(store, store.one('runs', run['id']), folder)
    assert 'research_handoff' not in json.loads((folder / 'input.json').read_text())  # fresh deep run: nothing to carry
    research_budget.reserve_search(store, run['id'], 2)
    handoff = {'learnings': [{'summary': '上半年收入 1200 万美元', 'source_id': source['id'], 'locator': 'line 1'}],
               'follow_ups': ['对手口径'], 'covered': ['本期收入'], 'open_questions': ['市场份额']}
    round_dir = store.root / 'research' / run['id'] / 'rounds' / '1'
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / 'handoff.json').write_text(json.dumps(handoff, ensure_ascii=False), encoding='utf-8')
    research_plan.finish_round(store, run['id'])
    prompt = generation_prompt(store, store.one('runs', run['id']), folder)
    payload = json.loads((folder / 'input.json').read_text())
    carried = payload['research_handoff']
    assert carried['round_index'] == 1 and carried['learnings'][0]['source_id'] == source['id']
    assert carried['follow_ups'] == ['对手口径'] and carried['unverified'] == 0
    assert payload['research_budget_status']['remaining']['search_requests'] == 3
    assert 'research_handoff' in prompt and '剩余预算' in prompt and '第 1 轮的交接' in prompt
    (round_dir / 'handoff.json').write_text(json.dumps({'learnings': [{'summary': '裸 URL', 'url': 'https://example.test/x'}]},
                                                       ensure_ascii=False), encoding='utf-8')
    prompt = generation_prompt(store, store.one('runs', run['id']), folder)  # a bad handoff never crashes prompting
    carried = json.loads((folder / 'input.json').read_text())['research_handoff']
    assert carried['invalid'] is True and carried['errors'][0]['code'] == 'bare_url'
    assert '不采信其中 learnings' in prompt


def test_exhausted_deep_run_ends_handed_off_instead_of_failed(tmp_path, monkeypatch):
    from briefloop.chat_tools import workspace_action
    from briefloop.runtime import Worker
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'search_provider': 'duckduckgo'})
    result = workspace_action(store, {'action': 'generate', 'requirements': {
        'title': 'Deep report', 'objective': 'Research a topic', 'allow_web': True,
        'research_budget': {'search_requests': 1, 'candidate_urls': 4, 'source_pages': 2}}})
    run_id = result['run_id']
    workspace_action(store, {'action': 'freeze_research_plan', 'run_id': run_id, 'preset': 'deep',
                             'structure': {'breadth': 1, 'depth': 2}})
    html, raw = ddg_html(['https://example.test/doc'])
    monkeypatch.setattr(duckduckgo, '_post', lambda form: (html, raw))
    from briefloop import sources
    page = b'<html><head><title>Example Doc</title></head><body><p>Capacity 45 MW.</p></body></html>'
    monkeypatch.setattr(sources, '_fetch_bytes', lambda url: (page, 'text/html; charset=utf-8', 'utf-8'))

    class ScoutRuntime:
        cancelled = threading.Event()

        def execute(self, job, prompt, folder, on_tick):
            assert 'research_handoff' not in prompt  # round 1 has nothing to inherit
            found = websearch.search('first query', store=store, run_id=run_id)
            assert found['status'] == 'ok'
            exhausted = websearch.search('second query', store=store, run_id=run_id)
            assert exhausted['status'] == 'budget_exhausted' and '交接' in exhausted['message']
            registered = websearch.extract(store, ['https://example.test/doc'], run_id=run_id)['sources'][0]
            handoff = {'learnings': [{'summary': '装机容量 45 MW', 'source_id': registered['id'], 'locator': 'line 1'}],
                       'follow_ups': [], 'covered': ['本期装机'], 'open_questions': ['预算内未覆盖的对手口径']}
            handoff_path = store.root / 'research' / run_id / 'rounds' / '1' / 'handoff.json'
            handoff_path.parent.mkdir(parents=True, exist_ok=True)
            handoff_path.write_text(json.dumps(handoff, ensure_ascii=False), encoding='utf-8')
            workspace_action(store, {'action': 'finish_research_round', 'run_id': run_id,
                                     'gaps': [{'description': '搜索预算耗尽，对手口径未覆盖'}],
                                     'summary': '预算耗尽，保留证据交接'})
            (folder / 'draft.json').write_text(json.dumps({'title': 'Deep report',
                                                           'markdown': 'Capacity 45 MW.'}), encoding='utf-8')
            on_tick()
            return {}

    worker = Worker(store, ScoutRuntime())
    generated = worker.generate(store.one('jobs', result['job_id']), score=False)
    assert generated['version_id']  # the run completes; budget exhaustion never fails the job
    plan = research_plan.frozen(store, run_id)
    first = next(info for info in plan['rounds'].values() if info['index'] == 1)
    assert first['status'] == 'closed' and first['outcome']['summary'] == '预算耗尽，保留证据交接'
    assert first['gaps'][0]['description'] == '搜索预算耗尽，对手口径未覆盖'
    snapshot = research_budget.snapshot(store, run_id)
    assert snapshot['exhausted'] is True and snapshot['remaining']['search_requests'] == 0
    written = json.loads((store.root / 'research' / run_id / 'rounds' / '1' / 'handoff.json').read_text())
    assert check_handoff(store, run_id, written)['learnings'][0]['status'] == '已引用'
    with pytest.raises(research_plan.AdmissionError, match='没有可用的联网轮次'):
        websearch.search('third query', store=store, run_id=run_id)  # controlled research stops in the handed-off state
