"""Commands exposed to an Analyst follow the real writer version contract."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

import pytest

from briefloop import analyst, analyst_drafts as drafts
from briefloop.chat_store import ChatStore
from test_native_analyst import setup


def test_analyst_cli_examples_update_json_version_then_check_and_submit(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    folder = store.root / 'writer'
    captured = []

    class BeforeModelDispatch(Exception):
        pass

    class Runtime:
        def execute(self, job, prompt, folder):
            captured.append(prompt)
            raise BeforeModelDispatch()

    job = {'id': 'job_cli_contract', 'kind': 'generate', 'payload': json.dumps({
        'run_id': run['id'], 'agent_backend': 'codex', 'runtime': {'model': 'no-model'},
        'writer_input_protocol': 'writer_input_v1'})}
    with pytest.raises(BeforeModelDispatch):
        analyst.run(store, Runtime(), job, run['id'], folder, 'codex', **inputs)
    prompt = captured[0]
    assert 'JSON 的 base_revision 字段' in prompt
    assert '这些操作不传 --revision' in prompt
    examples = {}
    for command in re.findall(r'`([^`]+)`', prompt):
        if ' writer --run ' in command:
            argv = shlex.split(command)
            examples[argv[argv.index('--operation') + 1]] = argv
    assert set(examples) == {'write_report', 'OPERATION', 'check_draft', 'submit_draft', 'read_draft'}

    # The fixture creates a real local candidate scope, without starting a model.
    chat = ChatStore(store)
    session = chat.create('writer CLI contract', {'model': 'no-model'}, folder)
    message = chat.message(session['id'], 'write', status='delivered')
    (folder / 'conversation.json').write_text(json.dumps({
        'session_id': session['id'], 'message_id': message['id']}))
    config = drafts.file_config(store, run['id'], folder / 'draft.json')
    body = f'收入增长20%。[@{source["id"]}]'
    (folder / 'article.md').write_text(body)

    def cli(example, replacements=None, extra=(), succeeds=True):
        replacements = replacements or {}
        argv = [replacements.get(part, part) for part in examples[example]] + list(extra)
        env = {**os.environ, 'PYTHONPATH': str(Path(analyst.__file__).resolve().parents[1])}
        proc = subprocess.run(argv, env=env, cwd=folder, capture_output=True, text=True)
        assert (proc.returncode == 0) is succeeds, proc.stdout + proc.stderr
        return json.loads(proc.stdout)

    first = cli('write_report')['revision']
    params = folder / 'citations.json'
    params.write_text(json.dumps({'base_revision': first, 'records': [{
        'source_id': source['id'], 'locator': 'line 1', 'excerpt': store.source_text(source['id'])}]}))
    update_args = {'OPERATION': 'update_citations', 'INPUT_JSON': str(params)}

    # Reproduce the real failure without relaxing the schema or changing a draft.
    refused = cli('OPERATION', update_args, extra=('--revision', first), succeeds=False)
    assert 'extra_forbidden' in refused['error'] and 'revision' in refused['error']
    assert drafts._read(drafts._root(store, config) / 'current.json')['revision'] == first

    updated = cli('OPERATION', update_args)['revision']
    assert updated != first
    cli('OPERATION', update_args, succeeds=False)  # stale JSON base_revision remains refused
    assert drafts._read(drafts._root(store, config) / 'current.json')['revision'] == updated
    assert cli('check_draft', {'REVISION': updated})['revision'] == updated
    assert cli('submit_draft', {'REVISION': updated})['revision'] == updated
    assert cli('read_draft')['revision'] == updated
    saved = json.loads((folder / 'draft.json').read_text())
    assert saved['markdown'] == body
    assert saved['citations'][0]['source_id'] == source['id']
