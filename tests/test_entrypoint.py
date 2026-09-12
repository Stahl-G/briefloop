import json
import os
from pathlib import Path
import subprocess

from briefloop._entrypoint import command


def test_tool_keeps_its_installation_after_agent_changes_directory(tmp_path):
    shadow = tmp_path / 'briefloop'
    shadow.mkdir()
    (shadow / '__init__.py').write_text('raise RuntimeError("Wrong BriefLoop checkout")')
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'action': 'workflows'}))
    result = subprocess.run(command('tool', '--workspace', tmp_path / 'workspace',
                                    'workspace-action', '--request', request),
                            cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(tmp_path)},
                            capture_output=True, text=True, check=True)
    assert {row['id'] for row in json.loads(result.stdout)['workflows']} == {'general_report', 'business_report'}
