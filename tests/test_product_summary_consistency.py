"""Check a few reader-facing claims against the actual product registries/config."""
from pathlib import Path
import runpy

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
