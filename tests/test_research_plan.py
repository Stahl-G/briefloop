import pytest
from briefloop import research_budget as budget
from briefloop import research_plan
from briefloop.store import Store


def quality_run(tmp_path, *, quality=True, values=None):
    store = Store(tmp_path)
    req = {'title': 'Report', 'objective': 'o', 'allow_web': True}
    if values:
        req['research_budget'] = values
    options = {'research_protocol': 'quality_v1'} if quality else {}
    return store, store.create_run(req, [], **options)


def test_quality_run_requires_freeze_before_any_controlled_request(tmp_path):
    store, run = quality_run(tmp_path)
    assert research_plan.is_quality(store, run['id'])
    with pytest.raises(research_plan.AdmissionError, match='尚未冻结'):
        budget.reserve_search(store, run['id'], 5)
    with pytest.raises(research_plan.AdmissionError, match='尚未冻结'):
        budget.reserve_pages(store, run['id'], ['https://example.test/a'])
    plan = research_plan.freeze(store, run['id'])
    assert plan['budget'] == {'search_requests': 12, 'candidate_urls': 60, 'source_pages': 18}
    reserved = budget.reserve_search(store, run['id'], 5)
    assert reserved['round_id'] == plan['current_round_id']
    entry = research_plan.pending_requests(store, run['id'])[reserved['request_id']]
    assert entry == {'operation': 'search', 'round_id': plan['current_round_id'], 'status': 'reserved', 'created': entry['created']}


def test_freeze_is_idempotent_and_refuses_a_different_plan(tmp_path):
    store, run = quality_run(tmp_path)
    first = research_plan.freeze(store, run['id'])
    again = research_plan.freeze(store, run['id'])
    assert again['plan_fingerprint'] == first['plan_fingerprint']
    with pytest.raises(ValueError, match='不同的研究计划'):
        research_plan.freeze(store, run['id'], preset='quick')


def test_freeze_cannot_exceed_the_authorized_budget(tmp_path):
    store, run = quality_run(tmp_path, values={'search_requests': 4, 'candidate_urls': 20, 'source_pages': 6})
    with pytest.raises(ValueError, match='超过已授权的搜索额度'):
        research_plan.freeze(store, run['id'], structure={'breadth': 6, 'depth': 2})
    plan = research_plan.freeze(store, run['id'], structure={'breadth': 2, 'depth': 2})
    assert plan['budget']['search_requests'] == 4
    assert plan['structure'] == {'breadth': 2, 'depth': 2, 'parallel': 2}


def test_legacy_run_keeps_unmetered_behavior_without_a_plan(tmp_path):
    store, run = quality_run(tmp_path, quality=False)
    assert not research_plan.is_quality(store, run['id'])
    reserved = budget.reserve_search(store, run['id'], 5)
    assert reserved['round_id'] is None
    assert research_plan.pending_requests(store, run['id']) == {}
    page = budget.reserve_pages(store, run['id'], ['https://example.test/b'])
    assert page['round_id'] is None


def test_closed_round_blocks_new_requests(tmp_path):
    store, run = quality_run(tmp_path)
    plan = research_plan.freeze(store, run['id'])
    stored = research_plan.frozen(store, run['id'])
    stored['rounds'][plan['current_round_id']]['status'] = 'closed'
    store.set_meta('research_plan:' + run['id'], stored)
    with pytest.raises(research_plan.AdmissionError, match='没有可用的联网轮次'):
        budget.reserve_search(store, run['id'], 5)
