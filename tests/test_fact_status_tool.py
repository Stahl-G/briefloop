"""fact-status 工具链路：agent 交 JSON 文件，Python 只做确定性校验落库。

好契约经 check_fact_result 通过、写入 fact_checks 并收束阶段；坏契约整体
结构化退回供 agent 自修——不落库、阶段不动。复用 Phase C1 的合成主张矩阵。
"""
import json
import pytest
from briefloop import fact_check, research_plan
from test_fact_check_contract import checked, good_result


def test_cli_accepts_good_contract_and_persists(tmp_path, monkeypatch, capsys):
    world = checked(tmp_path); store = world['store']
    path = tmp_path / 'fact-result.json'
    path.write_text(json.dumps(good_result(world), ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['briefloop', 'tool', '--workspace', str(store.root), 'fact-status',
                                     '--run', world['run']['id'], '--file', str(path), '--job', world['job']['id']])
    from briefloop.cli import main
    main(); out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'ok' and out['stage'] == 'completed'
    assert out['checked'] == 8 and out['unchecked'] == []
    saved = fact_check.get_record(store, out['record_id'])
    assert saved['run_id'] == world['run']['id'] and saved['data']['version_id'] == world['brief']['id']
    assert research_plan.frozen(store, world['run']['id'])['fact_check']['status'] == 'completed'
    events = [json.loads(row['data']) for row in store.rows(
        "SELECT data FROM events WHERE kind='fact_check' ORDER BY rowid")]
    assert any(event.get('action') == 'result' for event in events)  # --job 记入事件可审计


def test_cli_rejects_bad_contract_structured_and_persists_nothing(tmp_path, monkeypatch, capsys):
    world = checked(tmp_path); store = world['store']
    result = good_result(world)
    result['candidates'][0]['span_ids'] = ['https://example.com/announcement']  # 裸 URL 充当证据
    result['candidates'][1]['reason'] = ' '  # 缺一句依据
    result['candidates'] = result['candidates'][:7]  # completed 却缺覆盖（丢掉 unknown 候选）
    path = tmp_path / 'fact-result.json'
    path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['briefloop', 'tool', '--workspace', str(store.root), 'fact-status',
                                     '--run', world['run']['id'], '--file', str(path)])
    from briefloop.cli import main
    with pytest.raises(SystemExit) as info:
        main()
    assert info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'invalid' and payload['error'] == 'fact_check_contract'
    codes = {error['code'] for error in payload['errors']}
    assert {'bare_url', 'reason_missing', 'coverage_missing'} <= codes
    assert all(error['path'] and error['message'] for error in payload['errors'])  # 逐条可定位供自修
    assert store.rows('SELECT * FROM fact_checks') == []  # 拒绝不落库
    assert research_plan.frozen(store, world['run']['id'])['fact_check']['status'] == 'active'  # 阶段不动，可重交
