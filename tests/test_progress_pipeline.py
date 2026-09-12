from briefloop.progress import _pipeline


def test_pipeline_statuses_and_agent_lanes(tmp_path):
    assert [stage['status'] for stage in _pipeline(tmp_path, [])] == ['active', 'pending', 'pending', 'pending']
    (tmp_path / 'plan.json').write_text('{}')
    running = [{'role': 'Scout 1', 'status': 'running'}, {'role': 'Analyst', 'status': 'running'}]
    by = {stage['id']: stage for stage in _pipeline(tmp_path, running)}
    assert by['intake']['status'] == 'done'
    assert by['research']['status'] == 'active' and by['research']['agents'][0]['role'] == 'Scout 1'
    assert by['analysis']['status'] == 'active' and by['analysis']['agents'][0]['role'] == 'Analyst'
    assert by['evaluate']['status'] == 'pending'
    (tmp_path / 'draft.json').write_text('{}')
    by = {stage['id']: stage for stage in _pipeline(tmp_path, [{'role': 'Scout 1', 'status': 'completed'}])}
    assert by['research']['status'] == 'done' and by['analysis']['status'] == 'done'
    assert by['evaluate']['status'] == 'active'
    (tmp_path / 'assessment.json').write_text('{}')
    assert {stage['id']: stage for stage in _pipeline(tmp_path, [])}['evaluate']['status'] == 'done'
