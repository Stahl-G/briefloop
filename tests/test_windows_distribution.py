"""Opt-in wheel smoke checks, run from a copied test directory in native CI."""
from importlib.resources import files
import os
from pathlib import Path
import sysconfig

import pytest


pytestmark = pytest.mark.skipif(
    os.name != 'nt' or os.environ.get('BRIEFLOOP_WHEEL_ONLY') != '1',
    reason='Requires the isolated Windows wheel installation',
)


def test_imports_and_runtime_assets_come_from_installed_wheel():
    import briefloop
    import wikiskill

    installed = Path(sysconfig.get_path('purelib')).resolve()
    for package in (briefloop, wikiskill):
        assert Path(package.__file__).resolve().is_relative_to(installed)
    resources = files('briefloop')
    for name in ('static/app.js', 'static/runtime-bridge.mjs', 'process_host.py',
                 'skill_assets/tavily/SKILL.md', 'template_assets/general-report-zh-t1.docx'):
        assert resources.joinpath(name).is_file(), name
        assert resources.joinpath(name).read_bytes(), name
