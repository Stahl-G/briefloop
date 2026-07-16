"""Non-editable wheel resource and instrument-identity parity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from multi_agent_brief.semantic_evaluator.contracts import InstrumentConfig
from multi_agent_brief.semantic_evaluator.instrument import build_instrument_manifest
from multi_agent_brief.semantic_evaluator.resources import resource_sha256


REPO_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_PATHS = (
    ("profiles", "research_design_report_zh_v1.yaml"),
    ("prompts", "system_v1.txt"),
    ("prompts", "dimension_v1.txt"),
    ("baselines", "structured_checklist_zh_v1.yaml"),
)
WHEEL_RESOURCE_NAMES = {
    f"multi_agent_brief/semantic_evaluator/{'/'.join(parts)}"
    for parts in RESOURCE_PATHS
}

WHEEL_PROBE = r"""
import inspect
import os
from pathlib import Path

from multi_agent_brief.semantic_evaluator.contracts import InstrumentConfig
from multi_agent_brief.semantic_evaluator.instrument import build_instrument_manifest
import multi_agent_brief.semantic_evaluator.instrument as instrument_module
import multi_agent_brief.semantic_evaluator.normalization as normalization_module
import multi_agent_brief.semantic_evaluator.parser as parser_module
from multi_agent_brief.semantic_evaluator.resources import resource_sha256
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_text
import multi_agent_brief.semantic_evaluator.unit_planner as unit_planner_module
import multi_agent_brief.semantic_evaluator.validator as validator_module

resource_paths = (
    ("profiles", "research_design_report_zh_v1.yaml"),
    ("prompts", "system_v1.txt"),
    ("prompts", "dimension_v1.txt"),
    ("baselines", "structured_checklist_zh_v1.yaml"),
)
config = InstrumentConfig.model_validate(InstrumentConfig.minimal_example)
wheel_root = Path(os.environ["SEMANTIC_EVALUATOR_WHEEL_ROOT"]).resolve()
module_files = [
    Path(inspect.getfile(module)).resolve()
    for module in (
        instrument_module,
        normalization_module,
        parser_module,
        unit_planner_module,
        validator_module,
    )
]
payload = {
    "manifest": build_instrument_manifest(config).model_dump(mode="json"),
    "resources": {
        "/".join(parts): resource_sha256(*parts)
        for parts in resource_paths
    },
    "loaded_from_extracted_wheel": all(
        str(path).startswith(str(wheel_root)) for path in module_files
    ),
}
print(canonical_json_text(payload))
"""


def _source_identity() -> dict[str, object]:
    config = InstrumentConfig.model_validate(InstrumentConfig.minimal_example)
    return {
        "manifest": build_instrument_manifest(config).model_dump(mode="json"),
        "resources": {
            "/".join(parts): resource_sha256(*parts) for parts in RESOURCE_PATHS
        },
        "loaded_from_extracted_wheel": True,
    }


def test_wheel_contains_all_resources_and_matches_source_identity(
    tmp_path: Path,
) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1

    extract_root = tmp_path / "installed"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert WHEEL_RESOURCE_NAMES <= names
        archive.extractall(extract_root)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(extract_root)
    env["SEMANTIC_EVALUATOR_WHEEL_ROOT"] = str(extract_root)
    probe = subprocess.run(
        [sys.executable, "-c", WHEEL_PROBE],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    wheel_identity = json.loads(probe.stdout.splitlines()[-1])
    assert wheel_identity == _source_identity()
