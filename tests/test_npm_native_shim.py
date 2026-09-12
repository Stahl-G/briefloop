"""Exercise npm's native-executable shim without shell argument expansion."""
import json
import os
import shutil
import subprocess

import pytest

from briefloop.platform_support import OwnedProcess, cli_command


@pytest.mark.skipif(os.name != 'nt', reason='Windows npm entrypoints')
def test_native_npm_shim_executes_from_unicode_path_without_shell(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    root = tmp_path / '中文 npm 包'
    native = root / 'node_modules' / 'fixture' / 'runner.exe'
    native.parent.mkdir(parents=True)
    shutil.copyfile(node, native)
    shim = root / 'fixture.CMD'
    shim.write_text('@echo off', encoding='utf-8')
    shim.with_suffix('').write_text(
        '#!/bin/sh\nexec "$basedir/node_modules/fixture/runner.exe"   "$@"\n', encoding='utf-8')
    args = ['中文 空格', '&echo bad', '%PATH%', 'a"b', 'x|y']
    command = [str(shim), '-e', 'console.log(JSON.stringify(process.argv.slice(1)))', '--', *args]
    assert cli_command(command)[0] == str(native.resolve())
    process = OwnedProcess(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 0, errors
        assert json.loads(output) == args
    finally:
        process.close_tree()
