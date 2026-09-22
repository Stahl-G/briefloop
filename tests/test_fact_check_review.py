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


def test_candidates_and_reviewer_judgements_are_separately_queryable(tmp_path):
    world, record = admitted(tmp_path); store = world['store']; brief = world['brief']
    assert fact_check.view(store, brief['id'])['records'][0]['candidates'][0]['reviewer'] is None  # 尚无审阅时不编造结论
    job = store.enqueue('review', {'version_id': brief['id']})
    folder = store.root/'jobs'/job['id']
    prompts = []
    fiscal = world['claims']['fiscal']

    class Runtime:
        def execute(self, stage, prompt, target, **kwargs):
            prompts.append(prompt)
            packet = json.loads((target/'packet'/'index.json').read_text(encoding='utf-8'))
            claim_checks = [{'claim_id': claim['id'],
                             'status': 'insufficient_evidence' if claim['id'] == fiscal['id'] else 'supported_for_scope',
                             'reason': '候选判矛盾依据的是财年公告行；自然年度口径未见原文，材料不足' if claim['id'] == fiscal['id'] else '核对公告原文一致'}
                            for claim in world['claims'].values()]
            output = {'fingerprint': packet['fingerprint'], 'version_id': brief['id'], 'status': 'complete',
                      'summary': '复核了核查候选', 'coverage_scan_complete': True, 'claim_checks': claim_checks,
                      'requirement_checks': __import__('review_checks').requirement_checks(json.loads((target/'packet'/'target.json').read_text(encoding='utf-8'))),
                      'findings': [{'kind': 'insufficient_evidence', 'severity': 'major', 'claim_ids': [fiscal['id']],
                                    'block_ids': [fiscal['block']], 'report_quote': fiscal['quote'],
                                    'evidence': '公告原文为财年口径，自然年度披露未取得', 'description': '财年口径主张需补充原文'}]}
            (target/'review.json').write_text(json.dumps(output), encoding='utf-8')

    accepted = review.run_review(store, Runtime(), job, brief['id'], folder)
    # 指示词写明观察模式接纳规则：候选无权直接创建核心冲突或阻断交付
    assert '观察模式' in prompts[0] and '无权直接创建核心冲突或阻断交付' in prompts[0]
    assert '不代表主张真假' in prompts[0] and 'covers_this_version' in prompts[0]
    assert accepted['status'] == 'complete'
    # Reviewer 判断存独立记录（reviews.result），与候选记录（fact_checks）分开
    saved = json.loads(store.rows('SELECT result FROM reviews WHERE id=?', (accepted['id'],))[0]['result'])
    fiscal_check = next(check for check in saved['claim_checks'] if check['claim_id'] == fiscal['id'])
    assert fiscal_check['status'] == 'insufficient_evidence'
    # 网页视图同时可查两者：候选仍是 contradicted，Reviewer 结论是材料不足，互不改写
    panel = fact_check.view(store, brief['id'])
    entry = panel['records'][0]
    assert entry['candidates'][0]['reviewer'] is not None
    candidate = next(item for item in entry['candidates'] if item['claim_id'] == fiscal['id'])
    assert candidate['status'] == 'contradicted' and candidate['reviewer']['status'] == 'insufficient_evidence'
    assert candidate['anchors'][0]['quote'] == fiscal['quote']
    assert candidate['spans'][0]['source_name']  # 原文链接解析出来源与定位
    unselected = entry['unselected'][0]
    assert unselected['claim_id'] == world['claims']['internal']['id'] and unselected['statement']
    # 观察模式无硬门：候选存在不产生核心冲突，交付判断仍走既有规则
    assert store.rows('SELECT * FROM conflicts') == []


def test_stale_record_is_flagged_not_hidden(tmp_path):
    world, record = admitted(tmp_path); store = world['store']
    doc = json.loads(world['brief']['editor_document'])
    doc['content'][0]['content'][0]['text'] = '公司2026财年上半年营业收入650万美元，约合人民币3,900万元。'
    revised = store.revise(world['brief']['id'], editor_document=doc)
    panel = fact_check.view(store, revised['id'])
    assert panel['records'][0]['covers_version'] is False  # 改稿后旧核查不算新稿已核查，但保留可查
    target = review._snapshot(store, revised['id'])['fact_checks'][0]
    assert target['covers_this_version'] is False and target['candidates']  # 审阅包同样标注旧稿身份
