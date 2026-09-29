"""Phase D: observation-mode fact-check candidates reach the review packet and
the checks panel, while the Reviewer's own judgements stay separately queryable.

The fake runtime records the prompt and returns a review whose claim_checks
deliberately disagree with one "contradicted" candidate: the candidate keeps its
fact_checks identity, the Reviewer conclusion keeps its reviews.result identity,
and both remain readable side by side (fact_check.view). No hard gate appears.
"""
import json
import pytest
from test_fact_check_contract import checked, good_result
from briefloop import fact_check, review


def admitted(tmp_path):
    world = checked(tmp_path)
    record = fact_check.submit_result(world['store'], world['run']['id'], good_result(world),
                                      job_id=world['job']['id'])['record']
    return world, record


def test_packet_carries_candidates_and_unselected(tmp_path):
    world, record = admitted(tmp_path); store = world['store']
    fingerprint, files = review.build_packet(store, world['brief']['id'], store.root/'jobs'/'fact-review')
    target = json.loads((store.root/'jobs'/'fact-review/packet/target.json').read_text(encoding='utf-8'))
    assert target['snapshot_version'] >= 7
    section = target['fact_checks']
    assert len(section) == 1 and section[0]['id'] == record['id']
    assert section[0]['covers_this_version'] is True
    assert section[0]['execution']['status'] == 'completed'
    # 查了什么：每条候选带四态、依据与证据/查询 id 进包
    by_claim = {item['claim_id']: item for item in section[0]['candidates']}
    assert set(record['selection']['claim_ids']) <= set(by_claim)
    assert by_claim[world['claims']['fiscal']['id']]['status'] == 'contradicted'
    assert by_claim[world['claims']['unit']['id']]['query_ids'] and by_claim[world['claims']['unit']['id']]['span_ids']
    # 没查什么：未选主张带原因
    unselected = section[0]['selection']['unselected']
    assert unselected[0]['claim_id'] == world['claims']['internal']['id'] and unselected[0]['reason']
    # 旧快照语义不含核查候选：重放 v6 审阅时不冒充
    assert 'fact_checks' not in review._snapshot(store, world['brief']['id'], 6)


def test_stale_record_is_flagged_not_hidden(tmp_path):
    world, record = admitted(tmp_path); store = world['store']
    doc = json.loads(world['brief']['editor_document'])
    doc['content'][0]['content'][0]['text'] = '公司2026财年上半年营业收入650万美元，约合人民币3,900万元。'
    revised = store.revise(world['brief']['id'], editor_document=doc)
    panel = fact_check.view(store, revised['id'])
    assert panel['records'][0]['covers_version'] is False  # 改稿后旧核查不算新稿已核查，但保留可查
    target = review._snapshot(store, revised['id'])['fact_checks'][0]
    assert target['covers_this_version'] is False and target['candidates']  # 审阅包同样标注旧稿身份
