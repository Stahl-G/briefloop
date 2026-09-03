from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import zipfile

import pytest

from tests.test_runtime_host_continue_v2 import (
    _authorized_workspace,
    _service,
    _write_current_role_proposal,
)

from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication,
)
from multi_agent_brief.runtime_host_v2 import (
    build_finalized_local_review_projection,
)


ROOT = Path(__file__).parents[1]


def _ambient_wheel_build_backend_available() -> bool:
    try:
        import setuptools.build_meta  # noqa: F401
    except Exception:
        return False
    return True


# The wheel e2e tests build with --no-build-isolation, so the ambient
# interpreter must provide the setuptools PEP 517 backend itself. Homebrew
# Python ships without setuptools, for example.
_WHEEL_BACKEND_SKIP = pytest.mark.skipif(
    not _ambient_wheel_build_backend_available(),
    reason="pip wheel --no-build-isolation requires setuptools.build_meta in the ambient interpreter",
)


def _real_finalized_local_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create one verifier-valid finalized-local history without test-only Gate seams."""

    workspace = _authorized_workspace(tmp_path)
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.render.webbrowser.open",
        lambda _uri: False,
    )
    service = _service(workspace)
    for _ in range(12):
        result = service.continue_authorized()
        if result.status == "finalized_local":
            assert result.reason_code == "local_finalization_complete"
            return workspace
        assert result.status == "role_work_required", result.reason_code
        _write_current_role_proposal(workspace, result)
    raise AssertionError("real finalized-local workspace did not terminate")


def _wheel_e2e_command(
    *,
    script_path: os.PathLike[str],
    workspace: os.PathLike[str],
    installed: os.PathLike[str],
) -> list[str]:
    return [sys.executable, str(script_path), str(workspace), str(installed)]


@_WHEEL_BACKEND_SKIP
@pytest.mark.skipif(
    not supports_retained_directory_publication(),
    reason="successful finalized-local projection is unavailable on this platform",
)
def test_finalized_local_review_projection_source_and_wheel_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _real_finalized_local_workspace(tmp_path, monkeypatch)
    source_payload = build_finalized_local_review_projection(workspace).model_dump(
        mode="json", exclude_unset=False
    )
    facts = source_payload["facts"]
    assert facts["terminal_state"] == "finalized_local"
    assert facts["terminal_action_fingerprint"]
    assert facts["finalization_receipt_id"]
    assert facts["report"]["render_receipt_id"]
    assert facts["report"]["artifact_revision"] > 0
    assert facts["report"]["markdown_utf8"]
    assert facts["gate_bindings"]
    assert facts["facts_fingerprint"]

    build_root = tmp_path / "build-root"
    build_root.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", build_root / "README.md")
    shutil.copytree(ROOT / "src", build_root / "src")
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
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel_path = next(wheel_dir.glob("briefloop-*.whl"))
    installed = tmp_path / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(installed)
    script = textwrap.dedent(
        """
        import json
        from pathlib import Path
        import sys

        import multi_agent_brief
        from multi_agent_brief.runtime_host_v2 import (
            build_finalized_local_review_projection,
        )

        workspace = Path(sys.argv[1])
        installed = Path(sys.argv[2]).resolve()
        assert Path(multi_agent_brief.__file__).resolve().is_relative_to(installed)
        projection = build_finalized_local_review_projection(workspace)
        print(json.dumps(
            projection.model_dump(mode="json", exclude_unset=False),
            ensure_ascii=False,
            sort_keys=True,
        ))
        """
    )
    script_path = tmp_path / "wheel_finalized_local_review_facts.py"
    script_path.write_bytes(script.encode("utf-8"))
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(installed)
    run = subprocess.run(
        _wheel_e2e_command(
            script_path=script_path,
            workspace=workspace,
            installed=installed,
        ),
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout) == source_payload
