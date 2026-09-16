"""Check a few reader-facing claims against the actual product registries/config."""
import json
from pathlib import Path
import re
import runpy

from briefloop.backends import BACKENDS
from briefloop.templates import BUILTIN_TEMPLATES


ROOT = Path(__file__).resolve().parents[1]


def test_template_summary_matches_build_plan_catalog_and_assets():
    builder = runpy.run_path(str(ROOT / 'scripts/build_builtin_template.py'))
    planned = {f"{genre['stem']}-{theme['code']}.docx"
               for genre, theme in builder['builtin_variants']()}
    catalog = {document for document, _, _ in BUILTIN_TEMPLATES}
    shipped = {path.name for path in (ROOT / 'src/briefloop/template_assets').glob('*.docx')}
    assert planned == catalog == shipped, 'Template build plan, catalog or shipped assets drifted'
    assert len(catalog) == len(BUILTIN_TEMPLATES), 'Duplicate template catalog entries'
    assert f'contains {len(catalog)} templates:' in builder['__doc__'], 'Update the builder summary count'


def test_architecture_backend_summary_matches_selectable_registry():
    text = (ROOT / 'docs/architecture.md').read_text(encoding='utf-8')
    documented = re.search(r'后端可选：`([^`]+)`', text)
    assert documented, 'Architecture must state the selectable backend registry'
    assert documented[1].split('/') == list(BACKENDS), 'Update the architecture backend list'


def test_default_signing_summary_and_historical_release_are_explicit():
    desktop = ROOT / 'desktop/electron'
    package = json.loads((desktop / 'package.json').read_text(encoding='utf-8'))
    windows = (desktop / 'electron-builder.windows.cjs').read_text(encoding='utf-8')
    force = re.search(r'forceCodeSigning:\s*(true|false)\b', windows)
    assert force, 'Update this check if Windows signing configuration moves'
    state = lambda enabled: '开启' if enabled else '关闭'
    expected = (f"默认桌面构建：Mac 代码签名={state(package['build']['mac']['identity'] is not None)}；"
                f"DMG 签名={state(package['build']['dmg']['sign'])}；"
                f"Windows 强制代码签名={state(force[1] == 'true')}。")
    assert expected in (ROOT / 'docs/architecture.md').read_text(encoding='utf-8'), 'Default signing summary drifted'
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    historical = changelog.split('## 0.20.0 —', 1)[1].split('\n## ', 1)[0]
    assert '0.20.0 默认桌面安装包未签名，Mac 包也未公证' in historical, 'Keep historical release status unambiguous'
