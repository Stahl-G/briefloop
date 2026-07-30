"""Source/non-editable-wheel parity for Store-qualified PF-LAJ replay."""

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

from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication,
)


ROOT = Path(__file__).resolve().parents[1]


_PROBE = r"""
import json
import os
from pathlib import Path
import sys

import pytest

import multi_agent_brief
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.product.brief_html import build_brief_pages_data
from multi_agent_brief.product.post_final_assessment import PostFinalAssessmentService
from multi_agent_brief.product.post_final_review import PostFinalReviewService
from multi_agent_brief.product.review_session.launcher import (
    launch_actionable_review_session,
)
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
)
import multi_agent_brief.semantic_evaluator.runner as runner_module
from tests.test_post_final_assessment import (
    _fixture_service,
    _policy_payload,
    _schema9_finalized_local_workspace_upgraded,
)
from tests.test_post_final_human_review import _disposition_payload


mode = sys.argv[1]
workspace = Path(sys.argv[2])
expected_package_root = Path(sys.argv[3]).resolve()
package_file = Path(multi_agent_brief.__file__).resolve()
if not package_file.is_relative_to(expected_package_root):
    raise RuntimeError("package root mismatch")

if mode == "source":
    patch = pytest.MonkeyPatch()
    try:
        workspace, _run_id, _historical = (
            _schema9_finalized_local_workspace_upgraded(workspace.parent, patch)
        )
        calls = []
        service = _fixture_service(workspace, calls, terminal_mode="finding")
        if not service.policy_set(_policy_payload())["ok"]:
            raise RuntimeError("policy did not commit")
        patch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
        patch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
        assessed = service.assess()
        if not assessed.get("ok") or assessed.get("status") != "available":
            raise RuntimeError(f"source assessment failed: {assessed!r}")
        provider_calls = len(calls)
        review = PostFinalReviewService(workspace)
        review_status = review.review_status()
        finding = review_status["dispositions"][0]
        accepted = review.record_disposition(
            _disposition_payload(
                review_status,
                finding,
                request_id="wheel-shared-accept-1",
                decision="accept",
            )
        )
        draft = review.append_guidance_draft(
            {
                "schema_version": "briefloop.post_final_guidance_draft_input.v1",
                "human_actor_id": "human-reviewer-1",
                "human_request_id": "wheel-shared-draft-1",
                "assessment_result_id": review_status["assessment_result_id"],
                "finding_id": finding["finding_id"],
                "disposition_id": accepted["disposition_id"],
                "guidance_text": "Keep the conclusion within the report constraints.",
            }
        )
        review.approve_guidance(
            {
                "schema_version": "briefloop.post_final_guidance_status_input.v1",
                "human_actor_id": "human-reviewer-1",
                "human_request_id": "wheel-shared-approve-1",
                "guidance_id": draft["guidance_id"],
                "draft_revision": draft["draft_revision"],
            }
        )
        review_status = review.review_status()
        finding = review_status["dispositions"][0]
        review.record_disposition(
            _disposition_payload(
                review_status,
                finding,
                request_id="wheel-shared-defer-1",
                decision="defer",
            )
        )
    finally:
        patch.undo()
elif mode == "wheel":
    run_id = None
    provider_calls = 0
    os.environ.pop(ANTHROPIC_API_KEY_SETTING, None)
    runner_module.metadata.version = lambda _name: (_ for _ in ()).throw(
        AssertionError("wheel exact replay touched distribution metadata")
    )
    service = PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("wheel exact replay touched adapter")
        ),
    )
    current = service.status()
    if not current.get("assessment_request_id"):
        raise RuntimeError(f"missing request: {current!r}")
    assessed = service.retry(current["assessment_request_id"])
    if not assessed.get("ok") or not assessed.get("replayed"):
        raise RuntimeError(f"wheel replay failed: {assessed!r}")
    review = PostFinalReviewService(workspace)
    review_status = review.review_status()
    finding = review_status["dispositions"][0]
    replayed_disposition = review.record_disposition(
        _disposition_payload(
            review_status,
            finding,
            request_id="wheel-shared-defer-1",
            decision="defer",
        )
    )
    if not replayed_disposition.get("replayed"):
        raise RuntimeError("wheel Human disposition did not replay")
    launched = launch_actionable_review_session(
        workspace,
        open_browser=True,
        browser_open=lambda _url: False,
    )
    try:
        from urllib.request import urlopen

        with urlopen(launched.url.split("#", 1)[0], timeout=5) as response:
            if response.status != 200 or b"briefloop.brief_pages.data.v2" not in response.read():
                raise RuntimeError("wheel actionable page unavailable")
    finally:
        launched.server.close()
else:
    raise RuntimeError("unknown mode")

status = PostFinalAssessmentService(workspace).status()
review_status = PostFinalReviewService(workspace).review_status()
pages = build_brief_pages_data(workspace)
with SQLiteControlStore.open(workspace / "briefloop.db") as store:
    head = store.load_workspace_run_head()
    if head is None:
        raise RuntimeError("missing Store head")
    snapshot = store.load_snapshot(head.current_run_id)
    request = snapshot.post_final_assessment_requests[0]
    result = snapshot.post_final_assessment_results[0]

print(json.dumps({
    "provider_calls": provider_calls,
    "status": status["status"],
    "request_id": request.assessment_request_id,
    "request_fingerprint": request.request_fingerprint,
    "result_id": result.assessment_result_id,
    "result_fingerprint": result.result_fingerprint,
    "terminal_evidence_class": result.terminal_evidence_class,
    "semantic": {
        "status": pages["semantic"]["status"],
        "store_qualified": pages["semantic"]["store_qualified"],
        "finding_count": pages["semantic"]["coverage"]["finding_count"],
    },
    "human_review": {
        "current_decision": review_status["dispositions"][0]["current"]["decision"],
        "guidance_draft_count": len(review_status["guidance_drafts"]),
        "guidance_status_count": len(review_status["guidance_statuses"]),
        "next_run_consumption": review_status["next_run_consumption"],
        "provider_calls": review_status["provider_calls"],
        "improvement_status": pages["improvement"]["status"],
    },
}, sort_keys=True))
"""


def _run_probe(
    *,
    mode: str,
    workspace: Path,
    package_root: Path,
    script: Path,
    cwd: Path,
) -> dict[str, object]:
    command = [sys.executable]
    if sys.flags.optimize:
        command.append("-" + ("O" * sys.flags.optimize))
    command.extend((str(script), mode, str(workspace), str(package_root)))
    environment = dict(os.environ)
    environment.pop("BRIEFLOOP_LAJ_MESSAGES_API_KEY", None)
    environment["PYTHONPATH"] = os.pathsep.join((str(package_root), str(ROOT)))
    run = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if run.returncode:
        raise AssertionError(run.stdout + run.stderr)
    return json.loads(run.stdout)


@pytest.mark.skipif(
    not supports_retained_directory_publication(),
    reason="successful finalized-local assessment is unavailable on this platform",
)
def test_source_and_non_editable_wheel_replay_the_same_pf_laj_result(
    tmp_path: Path,
) -> None:
    """S28: installed consumers replay frozen evidence with no SDK/key/provider."""

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
        assert (
            "multi_agent_brief/control_store/migrations/0009.sql" in archive.namelist()
        )
        assert (
            "multi_agent_brief/control_store/migrations/0010.sql" in archive.namelist()
        )

    script = tmp_path / "pf_laj_wheel_probe.py"
    script.write_text(textwrap.dedent(_PROBE), encoding="utf-8")
    workspace = tmp_path / "workspace"
    source = _run_probe(
        mode="source",
        workspace=workspace,
        package_root=ROOT / "src",
        script=script,
        cwd=tmp_path,
    )
    wheel = _run_probe(
        mode="wheel",
        workspace=workspace,
        package_root=installed,
        script=script,
        cwd=tmp_path,
    )

    assert source["provider_calls"] == 9
    assert wheel["provider_calls"] == 0
    assert {key: source[key] for key in source if key != "provider_calls"} == {
        key: wheel[key] for key in wheel if key != "provider_calls"
    }
    assert wheel["semantic"]["status"] == "available"
    assert wheel["semantic"]["store_qualified"] is True
    assert wheel["semantic"]["finding_count"] >= 1
    assert wheel["human_review"] == {
        "current_decision": "defer",
        "guidance_draft_count": 1,
        "guidance_status_count": 1,
        "next_run_consumption": "not_shipped",
        "provider_calls": 0,
        "improvement_status": "available",
    }
