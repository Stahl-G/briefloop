from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import textwrap
import zipfile

import pytest

from tests.test_runtime_host_v2 import _adapter
from tests.test_runtime_host_continue_v2 import (
    _authorized_workspace,
    _service,
    _write_current_role_proposal,
)

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication,
)
from multi_agent_brief.runtime_host_v2 import (
    RuntimeHostError,
    build_finalized_local_review_projection,
)
from multi_agent_brief.runtime_host_v2.initialization import (
    initialize_or_open_runtime,
)


ROOT = Path(__file__).parents[1]


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


def test_wheel_e2e_command_uses_a_script_file_on_windows() -> None:
    script_path = PureWindowsPath(r"C:\tmp\wheel_e2e.py")
    command = _wheel_e2e_command(
        script_path=script_path,
        workspace=PureWindowsPath(r"C:\tmp\workspace"),
        installed=PureWindowsPath(r"C:\tmp\installed"),
    )

    assert command[1] == str(script_path)
    assert "-c" not in command


def test_source_and_non_editable_wheel_hardlink_intake_parity(
    tmp_path: Path,
) -> None:
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
    if build.returncode != 0:
        raise AssertionError(build.stdout + build.stderr)
    wheel_path = next(wheel_dir.glob("briefloop-*.whl"))
    installed = tmp_path / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(installed)

    script = textwrap.dedent(
        """
        import json
        import os
        from pathlib import Path
        import sys

        import multi_agent_brief
        from multi_agent_brief.contracts.v2 import IntegrityCheckRequest
        from multi_agent_brief.intake_v2.service import IntakeService
        from tests.test_control_store_intake_v2 import (
            CLOCK as INTAKE_CLOCK,
            _seed_workspace,
            _source_request,
        )
        from tests.test_core_run_v2 import (
            CLOCK as CORE_CLOCK,
            RUN_ID,
            _execution_authorization,
            _initialize,
            _record,
            _store_revision,
            _workspace,
        )

        root = Path(sys.argv[1])
        expected_package_root = Path(sys.argv[2]).resolve()
        package_file = Path(multi_agent_brief.__file__).resolve()
        if not package_file.is_relative_to(expected_package_root):
            raise RuntimeError("package root mismatch")

        def replace_with_external_hardlink(path, outside):
            content = path.read_bytes()
            outside.write_bytes(content)
            path.unlink()
            try:
                os.link(outside, path)
            except OSError:
                return None
            target_info = path.stat()
            outside_info = outside.stat()
            if (target_info.st_dev, target_info.st_ino) != (
                outside_info.st_dev, outside_info.st_ino
            ):
                raise RuntimeError("hardlink identity mismatch")
            if target_info.st_nlink <= 1:
                raise RuntimeError("hardlink link count mismatch")
            return content

        root.mkdir()
        intake_workspace = root / "intake-workspace"
        _seed_workspace(intake_workspace)
        intake_request = _source_request(intake_workspace)
        intake_leaf = intake_request.parent / "source_content.pdf"
        if replace_with_external_hardlink(
            intake_leaf, root / "outside-intake-source.pdf"
        ) is None:
            print(json.dumps({"hardlink_supported": False}, sort_keys=True))
            raise SystemExit(0)
        intake_db = intake_workspace / "briefloop.db"
        intake_before = intake_db.read_bytes()
        intake_result = IntakeService(
            intake_workspace, clock=INTAKE_CLOCK
        ).submit_source(intake_request.relative_to(intake_workspace).as_posix())

        core_workspace = _workspace(root / "core-workspace")
        core_service = _initialize(
            core_workspace,
            execution_authorization=_execution_authorization(core_workspace),
        )
        doctor = core_service.doctor_check(
            _record(
                IntegrityCheckRequest,
                request_id="REQ-WHEEL-HARDLINK-DOCTOR-001",
                run_id=RUN_ID,
                expected_store_revision=_store_revision(core_workspace),
            )
        )
        if doctor.status != "committed":
            raise RuntimeError(f"doctor did not commit: {doctor.to_dict()!r}")
        core_leaf = core_workspace / "input" / "authorized-source.txt"
        if replace_with_external_hardlink(
            core_leaf, root / "outside-authorized-source.txt"
        ) is None:
            raise RuntimeError("hardlink support changed between rows")
        core_db = core_workspace / "briefloop.db"
        core_before = core_db.read_bytes()
        core_result = core_service.apply_authorized_source_pack()

        print(json.dumps({
            "hardlink_supported": True,
            "optimize": sys.flags.optimize,
            "intake": {
                "result": intake_result.to_dict(),
                "database_unchanged": intake_db.read_bytes() == intake_before,
            },
            "core": {
                "result": core_result.to_dict(),
                "database_unchanged": core_db.read_bytes() == core_before,
            },
        }, sort_keys=True))
        """
    )
    script_path = tmp_path / "wheel_hardlink_intake_parity.py"
    script_path.write_bytes(script.encode("utf-8"))

    def execute(label: str, package_root: Path) -> dict[str, object]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((str(package_root), str(ROOT)))
        optimization_flag = (
            "-" + ("O" * sys.flags.optimize) if sys.flags.optimize else None
        )
        command = [sys.executable]
        if optimization_flag is not None:
            command.append(optimization_flag)
        command.extend(
            [
                str(script_path),
                str(tmp_path / f"{label}-run"),
                str(package_root),
            ]
        )
        run = subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if run.returncode != 0:
            raise AssertionError(run.stdout + run.stderr)
        return json.loads(run.stdout)

    source_payload = execute("source", ROOT / "src")
    if not source_payload["hardlink_supported"]:
        pytest.skip("test filesystem does not support hardlinks")
    wheel_payload = execute("wheel", installed)
    if wheel_payload != source_payload:
        raise AssertionError(
            f"source/wheel payload mismatch: {source_payload!r} != {wheel_payload!r}"
        )
    expected = {
        "hardlink_supported": True,
        "optimize": sys.flags.optimize,
        "intake": {
            "result": {
                "error_code": "scratch_entry_unsafe",
                "status": "failed_uncommitted",
            },
            "database_unchanged": True,
        },
        "core": {
            "result": {
                "error_code": "source_pack_authorization_invalid",
                "status": "failed_uncommitted",
            },
            "database_unchanged": True,
        },
    }
    if source_payload != expected:
        raise AssertionError(f"unexpected hardlink payload: {source_payload!r}")


def test_non_editable_wheel_runtime_install_all_uses_explicit_source_repo(
    tmp_path: Path,
) -> None:
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
        from contextlib import redirect_stdout
        import io
        from pathlib import Path
        import sys

        import multi_agent_brief
        from multi_agent_brief.cli.init_wizard import create_demo_workspace
        from multi_agent_brief.cli.main import main

        workspace = Path(sys.argv[1])
        installed = Path(sys.argv[2]).resolve()
        source_repo = Path(sys.argv[3]).resolve()
        assert Path(multi_agent_brief.__file__).resolve().is_relative_to(installed)
        create_demo_workspace(workspace)
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = main([
                "runtime", "install", "--workspace", str(workspace),
                "--runtime", "all", "--repo-workdir", str(source_repo),
            ])
        assert result == 0, stream.getvalue()
        assert (workspace / ".codex/config.toml").is_file()
        assert (workspace / ".opencode").is_dir()
        assert (workspace / ".claude").is_dir()
        assert (workspace / "AGENTS.md").is_file()
        assert (workspace / "CLAUDE.md").is_file()
        assert "Installed workspace runtime kit for all" in stream.getvalue()
        """
    )
    script_path = tmp_path / "wheel_runtime_all.py"
    script_path.write_bytes(script.encode("utf-8"))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(installed)
    run = subprocess.run(
        [
            sys.executable,
            str(script_path),
            str(tmp_path / "wheel-runtime-all-workspace"),
            str(installed),
            str(ROOT),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr


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


def test_non_editable_wheel_runs_complete_dormant_core_spine(
    tmp_path: Path,
) -> None:
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
        r"""
        from contextlib import redirect_stdout
        from copy import deepcopy
        import hashlib
        from importlib import resources
        import io
        import json
        import os
        from pathlib import Path
        import shutil
        import sys

        import multi_agent_brief
        from multi_agent_brief.product.projection_platform import (
            supports_retained_directory_publication,
        )
        from multi_agent_brief.cli.init_wizard import create_demo_workspace
        from multi_agent_brief.cli.main import main
        from multi_agent_brief.contracts.v2 import (
            ArtifactSubmitRequest,
            AuditPromotionRequest,
            ClaimFreezeRequest,
            CoreRunInitializeRequest,
            GateCheckRequest,
            IntegrityCheckRequest,
            InvocationStartRequest,
            OwnedArtifactSubmitRequest,
            SourceCommitRequest,
            SourceProposal,
            StageCompleteRequest,
            SchemaRegistry,
        )
        from multi_agent_brief.control_store import SQLiteControlStore
        from multi_agent_brief.control_store.serialization import canonical_fingerprint
        from multi_agent_brief.core_run_v2.checkout import build_checkout_revision
        from multi_agent_brief.core_run_v2.publication import CheckoutPublicationEngine
        from multi_agent_brief.core_run_v2.policy import REQUIRED_AUDITOR_GATES
        from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
        from multi_agent_brief.product.init_web.submit import (
            SUBMISSION_SCHEMA,
            InitWebSubmitter,
        )
        from multi_agent_brief.product.init_web.staging import (
            MAX_SOURCE_AGGREGATE_BYTES as INIT_MAX_SOURCE_AGGREGATE_BYTES,
            MAX_SOURCE_MEMBER_BYTES as INIT_MAX_SOURCE_MEMBER_BYTES,
            MAX_SOURCE_MEMBERS as INIT_MAX_SOURCE_MEMBERS,
        )
        from multi_agent_brief.runtime_host_v2.service import (
            RuntimeHostService,
            _ROLE_OUTPUTS,
            _strict_proposal_violations,
        )
        from multi_agent_brief.runtime_host_v2.codex import (
            workspace_codex_adapter_loader,
        )
        from multi_agent_brief.runtime_host_v2.submission import (
            MAX_SOURCE_MEMBER_BYTES,
            MAX_SOURCE_PACK_BYTES,
            MAX_SOURCE_PACK_MEMBERS,
        )

        workspace = Path(sys.argv[1])
        installed = Path(sys.argv[2]).resolve()
        assert Path(multi_agent_brief.__file__).resolve().is_relative_to(installed)
        assert supports_retained_directory_publication() is (
            os.environ["BRIEFLOOP_TEST_RETAINED_PUBLICATION_CAPABILITY"] == "1"
        )
        migration_0004 = resources.files(
            "multi_agent_brief.control_store"
        ).joinpath("migrations", "0004.sql")
        assert migration_0004.is_file()
        assert "PRAGMA user_version=4;" in migration_0004.read_text(encoding="utf-8")
        migration_0005 = resources.files(
            "multi_agent_brief.control_store"
        ).joinpath("migrations", "0005.sql")
        assert migration_0005.is_file()
        assert "PRAGMA user_version=5;" in migration_0005.read_text(encoding="utf-8")
        migration_0006 = resources.files(
            "multi_agent_brief.control_store"
        ).joinpath("migrations", "0006.sql")
        assert migration_0006.is_file()
        assert "PRAGMA user_version=6;" in migration_0006.read_text(encoding="utf-8")
        migration_0007 = resources.files(
            "multi_agent_brief.control_store"
        ).joinpath("migrations", "0007.sql")
        assert migration_0007.is_file()
        assert "PRAGMA user_version=7;" in migration_0007.read_text(encoding="utf-8")
        migration_0008 = resources.files(
            "multi_agent_brief.control_store"
        ).joinpath("migrations", "0008.sql")
        assert migration_0008.is_file()
        assert "PRAGMA user_version=8;" in migration_0008.read_text(encoding="utf-8")
        migration_0009 = resources.files(
            "multi_agent_brief.control_store"
        ).joinpath("migrations", "0009.sql")
        assert migration_0009.is_file()
        assert "PRAGMA user_version=9;" in migration_0009.read_text(encoding="utf-8")
        assert callable(build_checkout_revision)
        assert CheckoutPublicationEngine.__module__.endswith(".publication")
        assert MAX_SOURCE_PACK_MEMBERS == 256
        assert MAX_SOURCE_MEMBER_BYTES == 16 * 1024 * 1024
        assert MAX_SOURCE_PACK_BYTES == 256 * 1024 * 1024
        assert INIT_MAX_SOURCE_MEMBERS == MAX_SOURCE_PACK_MEMBERS
        assert INIT_MAX_SOURCE_MEMBER_BYTES == MAX_SOURCE_MEMBER_BYTES
        assert INIT_MAX_SOURCE_AGGREGATE_BYTES == MAX_SOURCE_PACK_BYTES
        verifier_content = b"packaged exact source bytes\n"
        verifier_raw = b'{"packaged":true}\n'
        verifier_proposal = deepcopy(SourceProposal.full_example)
        verifier_proposal.update(
            content_sha256=hashlib.sha256(verifier_content).hexdigest(),
            raw_payload_sha256=hashlib.sha256(verifier_raw).hexdigest(),
        )
        verifier_outputs = {
            "source_proposal.json": json.dumps(
                verifier_proposal,
                sort_keys=True,
            ).encode("utf-8"),
            "source_content.bin": verifier_content,
            "source_raw.json": verifier_raw,
        }
        assert _strict_proposal_violations(
            _ROLE_OUTPUTS["source-provider"],
            verifier_outputs,
            expected_run_id=verifier_proposal["run_id"],
        ) == []
        verifier_outputs["source_content.bin"] += b"tampered"
        assert [
            item.field
            for item in _strict_proposal_violations(
                _ROLE_OUTPUTS["source-provider"],
                verifier_outputs,
                expected_run_id=verifier_proposal["run_id"],
            )
        ] == ["content_sha256"]

        binding_workspace = workspace.parent / "codex-binding-wheel"
        create_demo_workspace(binding_workspace)
        stream = io.StringIO()
        with redirect_stdout(stream):
            install_exit = main([
                "runtime", "install", "--workspace", str(binding_workspace),
                "--runtime", "codex",
            ])
            run_exit = main([
                "run", "--workspace", str(binding_workspace),
                "--runtime", "codex",
            ])
        assert install_exit == 0
        assert run_exit == 0
        runtime_reference = binding_workspace / (
            ".codex/skills/briefloop/references/controlstore-v2.md"
        )
        assert runtime_reference.is_file()
        reference_text = runtime_reference.read_text(encoding="utf-8")
        assert "RunSourceDiscoveryAuthorization" in reference_text
        assert "one exact Human-confirmed Tavily Search request" in reference_text
        assert "Search snippets are" in reference_text
        assert "claims-ineligible" in reference_text
        scout = binding_workspace / ".codex/agents/briefloop-scout.toml"
        scout.write_bytes(scout.read_bytes() + b"\n# wheel drift\n")
        stream = io.StringIO()
        with redirect_stdout(stream):
            assert main([
                "runtime", "next", "--workspace", str(binding_workspace),
            ]) == 1
        assert "runtime_adapter_binding_mismatch" in stream.getvalue()

        init_web_root = workspace.parent / "init-web-wheel"
        init_web_root.mkdir()
        init_submitter = InitWebSubmitter(base_dir=init_web_root)
        init_content = b"packaged init source\n"
        init_upload = init_submitter.stage_upload(
            session_id="wheel-init-session",
            filename="source.txt",
            stream=io.BytesIO(init_content),
            declared_length=len(init_content),
        )
        init_metadata = {
            "source_id": "SRC-WHEEL-INIT-001",
            "expected_content_sha256": init_upload["sha256"],
            "origin_type": "uploaded_file",
            "acquisition_method": "manual_upload",
            "material_kind": "uploaded_file",
            "provider": None,
            "original_url": None,
            "title": "Packaged init source",
            "publisher": "Example publisher",
            "published_at": "2026-07-22",
            "retrieved_at": "2026-07-23T00:00:00Z",
            "source_category": "other",
            "retrieval_source_type": "local_file",
            "underlying_evidence_type": "unknown",
            "raw_underlying_evidence_type": None,
            "document_kind": None,
            "opened_at": None,
            "resolved_at": None,
        }
        init_bindings = [{
            "metadata_index": 0,
            "upload_handle": init_upload["upload_handle"],
        }]
        init_preview = init_submitter.preview_source_manifest(
            session_id="wheel-init-session",
            body={
                "source_manifest_mode": "imported",
                "source_metadata": [init_metadata],
                "upload_bindings": init_bindings,
            },
        )
        status, response = init_submitter.submit({
            "schema_version": SUBMISSION_SCHEMA,
            "request_id": "REQ-WHEEL-INIT-WEB-001",
            "payload": {
                "workspace_target": "workspace",
                "selections": {
                    "company": "Wheel ExampleCo",
                    "industry_or_theme": "manufacturing",
                    "task_objective": "Prepare a packaged runtime brief.",
                    "audience": "management",
                    "focus_areas": ["operations"],
                    "output_formats": ["markdown"],
                    "web_search_mode": "disabled",
                    "output_extent": "balanced",
                    "output_language": "en",
                },
                "completion_target": "finalized_local",
                "repair_budget": 1,
                "source_manifest_mode": "imported",
                "source_metadata": init_preview["source_metadata"],
                "source_manifest": init_preview["source_manifest"],
                "upload_session_id": "wheel-init-session",
                "upload_bindings": init_preview["routing_bindings"],
                "human_confirmation": True,
            },
        })
        assert status == 200
        init_web_workspace = init_web_root / "workspace"
        assert (init_web_workspace / ".codex/config.toml").is_file()
        assert (init_web_workspace / "briefloop.db").is_file()
        stream = io.StringIO()
        with redirect_stdout(stream):
            assert main([
                "runtime", "next", "--workspace", str(init_web_workspace),
            ]) == 0
        assert json.loads(stream.getvalue())["run_id"] == response["run_id"]
        stream = io.StringIO()
        with redirect_stdout(stream):
            assert main([
                "runtime", "continue", "--workspace", str(init_web_workspace),
            ]) == 0
        continuation = json.loads(stream.getvalue())
        assert continuation["status"] == "role_work_required"
        assert continuation["current_stage"] == "scout"
        assert "trace" not in continuation

        init_service = RuntimeHostService(
            init_web_workspace,
            adapter_loader=workspace_codex_adapter_loader(init_web_workspace),
        )
        sequence = []

        def write_role_proposal(result):
            envelope_path = init_web_workspace / result.trace.envelope_path
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
            scratch = init_web_workspace / envelope["scratch_directory"]
            role_id = envelope["role_id"]
            run_id = envelope["run_id"]
            with SQLiteControlStore.open(init_web_workspace / "briefloop.db") as store:
                current = store.load_snapshot(run_id)
            if role_id == "scout":
                payload = deepcopy(SchemaRegistry.example(
                    "briefloop.candidate_claims_proposal.v2", "minimal"
                ))
                payload.update(run_id=run_id, proposal_id="PROP-WHEEL-CANDIDATES")
                payload["candidates"][0].update(
                    source_id=current.sources[0].source_id,
                    statement="ExampleCo opened a public pilot facility.",
                    evidence_text="packaged init source",
                )
                filename = "candidate_claims.json"
            elif role_id == "screener":
                candidate = next(
                    item for item in current.accepted_proposals
                    if item.proposal_kind == "candidate"
                )
                payload = deepcopy(SchemaRegistry.example(
                    "briefloop.screened_candidates_proposal.v2", "minimal"
                ))
                payload.update(
                    run_id=run_id,
                    proposal_id="PROP-WHEEL-SCREENED",
                    candidate_claims_proposal_id=candidate.proposal_id,
                )
                payload["decisions"][0]["candidate_id"] = "CAND-001"
                filename = "screened_candidates.json"
            elif role_id == "claim-ledger":
                screened = next(
                    item for item in current.accepted_proposals
                    if item.proposal_kind == "screened"
                )
                payload = deepcopy(SchemaRegistry.example(
                    "briefloop.claim_drafts_proposal.v2", "minimal"
                ))
                payload.update(
                    run_id=run_id,
                    proposal_id="PROP-WHEEL-DRAFTS",
                    screened_candidates_proposal_id=screened.proposal_id,
                )
                payload["drafts"][0]["source_ids"] = [current.sources[0].source_id]
                filename = "claim_drafts.json"
            elif role_id in {"analyst", "editor"}:
                repairing = role_id == "editor" and bool(
                    current.gate_repair_cycles
                )
                repetitions = 160 if repairing else 20
                body = (
                    "# ExampleCo public brief\n\n## Executive Summary\n\n"
                    + " ".join(
                        ["Wheel ExampleCo operations context"] * repetitions
                    )
                    + " ExampleCo opened a public pilot facility. [src:CL-0001]\n"
                )
                filename = (
                    "analyst_draft.md" if role_id == "analyst"
                    else "audited_brief.md"
                )
                (scratch / filename).write_text(body, encoding="utf-8")
                return
            elif role_id == "auditor":
                payload = deepcopy(SchemaRegistry.example(
                    "briefloop.audit_proposal.v2", "minimal"
                ))
                payload.update(
                    run_id=run_id,
                    proposal_id=(
                        "PROP-WHEEL-AUDIT-REPAIR"
                        if current.gate_repair_cycles
                        else "PROP-WHEEL-AUDIT"
                    ),
                    artifact_id="audited_brief",
                    artifact_revision=next(
                        item.current_revision
                        for item in current.artifacts
                        if item.artifact_id == "audited_brief"
                    ),
                    decision="pass",
                    findings=[],
                )
                filename = "audit_proposal.json"
            else:
                raise AssertionError(role_id)
            (scratch / filename).write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8"
            )

        current_result = init_service.continue_authorized()
        for _ in range(12):
            sequence.append((
                current_result.status,
                current_result.reason_code,
                current_result.trace.next_action.action_fingerprint,
            ))
            if current_result.status == "finalized_local":
                break
            if (
                sys.platform == "win32"
                and current_result.status == "needs_attention"
            ):
                break
            assert current_result.status == "role_work_required", sequence
            write_role_proposal(current_result)
            current_result = init_service.continue_authorized()
        else:
            raise AssertionError("packaged init run did not reach finalized_local")

        if sys.platform == "win32":
            assert [item[0] for item in sequence] == [
                "role_work_required",
                "role_work_required",
                "role_work_required",
                "needs_attention",
            ]
            assert [item[1] for item in sequence] == [
                "role_work_required",
                "role_work_required",
                "role_work_required",
                "checkout_publication_unsupported",
            ]
            assert current_result.trace.next_action.effect_kind == "claim_freeze"
            blocked_action_fingerprint = sequence[-1][2]
            with SQLiteControlStore.open(init_web_workspace / "briefloop.db") as store:
                verified_before_retry = CoreRunDomainVerifier().verify(
                    store,
                    response["run_id"],
                )
            init_snapshot = verified_before_retry.snapshot
            blocked_revision = init_snapshot.store_revision

            retry_result = init_service.continue_authorized()
            assert retry_result.status == "needs_attention"
            assert retry_result.reason_code == "checkout_publication_unsupported"
            assert (
                retry_result.trace.next_action.action_fingerprint
                == blocked_action_fingerprint
            )
            with SQLiteControlStore.open(init_web_workspace / "briefloop.db") as store:
                verified_after_retry = CoreRunDomainVerifier().verify(
                    store,
                    response["run_id"],
                )
            assert verified_after_retry.snapshot.store_revision == blocked_revision
        else:
            assert current_result.reason_code == "local_finalization_complete"
            assert current_result.trace.next_action.effect_kind == "finalized_local"
            assert current_result.presentation.status in {
                "opened",
                "browser_unavailable",
            }
            assert (
                current_result.presentation.relative_path
                == "output/brief_pages.html"
            )
            assert [item[0] for item in sequence] == [
                "role_work_required",
                "role_work_required",
                "role_work_required",
                "role_work_required",
                "role_work_required",
                "role_work_required",
                "role_work_required",
                "role_work_required",
                "finalized_local",
            ]
            with SQLiteControlStore.open(init_web_workspace / "briefloop.db") as store:
                init_snapshot = store.load_snapshot(response["run_id"])
                init_history = store.load_history()
            reader_record = next(
                item
                for item in init_snapshot.artifacts
                if item.artifact_id == "reader_brief"
            )
            reader_bytes = init_history.read_artifact_revision_bytes(
                response["run_id"],
                "reader_brief",
                reader_record.current_revision,
            )
            brief_html = (
                init_web_workspace / "output" / "brief_pages.html"
            ).read_text(encoding="utf-8")
            brief_data = json.loads(
                brief_html.split('id="brief-pages-data">', 1)[1].split(
                    "</script>",
                    1,
                )[0]
            )
            assert brief_data["brief"]["markdown"] == reader_bytes.decode("utf-8")
            assert brief_data["brief"]["artifact"] == {
                "artifact_id": "reader_brief",
                "revision": reader_record.current_revision,
                "sha256": hashlib.sha256(reader_bytes).hexdigest(),
            }
            assert str(init_web_workspace) not in brief_html
            assert len(init_snapshot.gate_repair_cycles) == 1
            assert len(init_snapshot.gate_repair_artifact_bindings) == 1
            assert len(init_snapshot.gate_repair_outcomes) == 1
            repair_binding = init_snapshot.gate_repair_artifact_bindings[0]
            assert repair_binding.prior_artifact.revision == 1
            assert repair_binding.successor_artifact.revision == 2
            assert init_snapshot.gate_repair_outcomes[0].disposition == "passed"

        if sys.platform == "win32":
            assert not init_snapshot.finalizations
        assert not init_snapshot.package_ready_records
        assert not init_snapshot.approvals
        assert not init_snapshot.delivery_authorizations
        assert not init_snapshot.delivery_attempts
        assert not init_snapshot.delivery_results

        create_demo_workspace(workspace)
        run_id = "RUN-WHEEL-CORE-V2-001"
        workspace_id = "WS-WHEEL-CORE-V2-001"
        now = "2026-07-15T12:00:00Z"
        counter = 0

        def record(model, **values):
            return model.model_validate(
                {"schema_version": model.schema_id, **values},
                strict=True,
            )

        def write_json(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            return path

        def request_path(payload, *, scope="cli", name="request"):
            global counter
            counter += 1
            return write_json(
                workspace
                / "scratch"
                / f"{scope}-{counter:03d}-{name}"
                / "submit_request.json",
                payload,
            ).relative_to(workspace).as_posix()

        def call(group, action, request, *, target=None, expected="committed"):
            target = workspace if target is None else target
            stream = io.StringIO()
            with redirect_stdout(stream):
                return_code = main([
                    group,
                    action,
                    "--workspace",
                    str(target),
                    "--request",
                    request,
                    "--json",
                ])
            lines = stream.getvalue().splitlines()
            assert len(lines) == 1, lines
            result = json.loads(lines[0])
            assert result["status"] == expected, result
            assert return_code == (0 if expected in {"committed", "replayed"} else 1)
            return result

        def revision(target=None):
            target = workspace if target is None else target
            with SQLiteControlStore.open(target / "briefloop.db") as store:
                return store.current_revision

        def snapshot(target=None):
            target = workspace if target is None else target
            with SQLiteControlStore.open(target / "briefloop.db") as store:
                return store.load_snapshot(run_id)

        def stage(stage_id, target=None):
            return next(
                item for item in snapshot(target).stage_states
                if item.stage_id == stage_id
            )

        def core(action, model, *, scope="cli", target=None, expected="committed"):
            target = workspace if target is None else target
            path = target / "scratch" / scope / "submit_request.json"
            write_json(path, model.model_dump(mode="json", exclude_unset=False))
            return call(
                "core-v2",
                action,
                path.relative_to(target).as_posix(),
                target=target,
                expected=expected,
            )

        def start(request_id, stage_id, role_id):
            result = core(
                "invocation-start",
                record(
                    InvocationStartRequest,
                    request_id=request_id,
                    run_id=run_id,
                    stage_id=stage_id,
                    role_id=role_id,
                    runtime="operator",
                    expected_store_revision=revision(),
                ),
                scope="cli",
            )
            return result["primary_record_id"]

        def complete(stage_id, artifacts, gate_ids=None, *, target=None, expected="committed"):
            target = workspace if target is None else target
            current = stage(stage_id, target)
            return core(
                "stage-complete",
                record(
                    StageCompleteRequest,
                    request_id=f"REQ-WHEEL-COMPLETE-{stage_id.upper()}",
                    run_id=run_id,
                    stage_id=stage_id,
                    reason=f"{stage_id} accepted output is complete",
                    expected_stage_revision=current.revision,
                    expected_store_revision=revision(target),
                    expected_artifact_revisions=[
                        {"artifact_id": artifact_id, "revision": artifact_revision}
                        for artifact_id, artifact_revision in artifacts
                    ],
                    expected_gate_evaluation_ids=gate_ids or [],
                ),
                target=target,
                expected=expected,
            )

        initialize = deepcopy(CoreRunInitializeRequest.minimal_example)
        initialize.update(
            request_id="REQ-WHEEL-INIT",
            run_id=run_id,
            workspace_id=workspace_id,
            role_topology="default",
            input_governance_required=False,
        )
        runtime_adapter = dict(initialize["runtime_adapter_binding"])
        runtime_adapter["run_id"] = run_id
        runtime_adapter.pop("binding_fingerprint", None)
        runtime_adapter["binding_fingerprint"] = canonical_fingerprint(
            runtime_adapter
        )
        initialize["runtime_adapter_binding"] = runtime_adapter
        call(
            "core-v2",
            "initialize",
            request_path(initialize, name="initialize"),
        )
        core(
            "doctor-check",
            record(
                IntegrityCheckRequest,
                request_id="REQ-WHEEL-DOCTOR",
                run_id=run_id,
                expected_store_revision=revision(),
            ),
        )

        planner = start(
            "REQ-WHEEL-INVOKE-PLANNER",
            "source-discovery",
            "source-planner",
        )
        candidates = workspace / "scratch" / planner / "source_candidates.yaml"
        candidates.parent.mkdir(parents=True, exist_ok=True)
        candidates.write_text("sources:\n  - SRC-WHEEL-001\n", encoding="utf-8")
        source_candidates_request = record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-WHEEL-ARTIFACT-SOURCES",
            run_id=run_id,
            artifact_id="source_candidates",
            invocation_id=planner,
            producer_tool_id=None,
            input_path=candidates.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
        if sys.platform == "win32":
            before_publication = snapshot()
            unsupported = core(
                "artifact-submit",
                source_candidates_request,
                scope=planner,
                expected="failed_uncommitted",
            )
            assert unsupported["error_code"] == "checkout_publication_unsupported"
            after_publication = snapshot()
            assert after_publication.store_revision == before_publication.store_revision
            assert len(after_publication.transactions) == len(
                before_publication.transactions
            )
            assert next(
                item
                for item in after_publication.artifacts
                if item.artifact_id == "source_candidates"
            ).current_revision == 0
            assert all(
                item.artifact_id != "source_candidates"
                for item in after_publication.artifact_revisions
            )
            print(json.dumps({
                "package_imported": True,
                "publication_error": unsupported["error_code"],
                "store_revision_unchanged": (
                    after_publication.store_revision
                    == before_publication.store_revision
                ),
            }, sort_keys=True))
            raise SystemExit(0)
        core(
            "artifact-submit",
            source_candidates_request,
            scope=planner,
        )

        provider = start(
            "REQ-WHEEL-INVOKE-PROVIDER",
            "source-discovery",
            "source-provider",
        )
        source_dir = workspace / "scratch" / provider
        source_dir.mkdir(parents=True, exist_ok=True)
        content = b"ExampleCo opened a public pilot facility on 2026-07-14.\n"
        content_path = source_dir / "source_content.txt"
        content_path.write_bytes(content)
        source_proposal = write_json(
            source_dir / "source_proposal.json",
            {
                "schema_version": "briefloop.source_proposal.v2",
                "proposal_id": "PROP-WHEEL-SOURCE",
                "run_id": run_id,
                "source_id": "SRC-WHEEL-001",
                "origin_type": "uploaded_file",
                "acquisition_method": "manual_upload",
                "material_kind": "uploaded_file",
                "provider": None,
                "locator": {
                    "kind": "file",
                    "path": content_path.relative_to(workspace).as_posix(),
                },
                "title": "Synthetic packaged source",
                "publisher": "Example regulator",
                "published_at": "2026-07-14",
                "retrieved_at": now,
                "source_category": "regulator",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "filing",
                "raw_underlying_evidence_type": None,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_media_type": "text/plain",
                "raw_payload_sha256": None,
                "raw_payload_media_type": None,
            },
        )
        source_request = record(
            SourceCommitRequest,
            request_id="REQ-WHEEL-SOURCE",
            run_id=run_id,
            invocation_id=provider,
            proposal_path=source_proposal.relative_to(workspace).as_posix(),
            content_path=content_path.relative_to(workspace).as_posix(),
            raw_payload_path=None,
            expected_store_revision=revision(),
        )
        source_request_path = write_json(
            source_dir / "submit_request.json",
            source_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "source",
            source_request_path.relative_to(workspace).as_posix(),
        )
        accepted_source = snapshot().sources[0]
        complete(
            "source-discovery",
            [
                ("source_candidates", 1),
                (
                    accepted_source.content_artifact_id,
                    accepted_source.content_artifact_revision,
                ),
            ],
        )
        complete("input-governance", [])

        scout = start("REQ-WHEEL-INVOKE-SCOUT", "scout", "scout")
        scout_dir = workspace / "scratch" / scout
        candidate_path = write_json(
            scout_dir / "candidate_claims.json",
            {
                "schema_version": "briefloop.candidate_claims_proposal.v2",
                "proposal_id": "PROP-WHEEL-CANDIDATE",
                "run_id": run_id,
                "created_at": now,
                "candidates": [
                    {
                        "candidate_id": "CAND-WHEEL-001",
                        "source_id": "SRC-WHEEL-001",
                        "statement": "ExampleCo opened a public pilot facility.",
                        "evidence_text": (
                            "ExampleCo opened a public pilot facility on 2026-07-14."
                        ),
                        "topic": "operations",
                        "claim_type": "fact",
                        "confidence": "high",
                    }
                ],
            },
        )
        candidate_request = record(
            ArtifactSubmitRequest,
            request_id="REQ-WHEEL-CANDIDATE",
            run_id=run_id,
            artifact_id="candidate_claims",
            invocation_id=scout,
            input_path=candidate_path.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
        )
        candidate_request_path = write_json(
            scout_dir / "submit_request.json",
            candidate_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "candidate",
            candidate_request_path.relative_to(workspace).as_posix(),
        )

        screening = start("REQ-WHEEL-INVOKE-SCREEN", "scout", "scout")
        screening_dir = workspace / "scratch" / screening
        screened_path = write_json(
            screening_dir / "screened_candidates.json",
            {
                "schema_version": "briefloop.screened_candidates_proposal.v2",
                "proposal_id": "PROP-WHEEL-SCREENED",
                "run_id": run_id,
                "candidate_claims_proposal_id": "PROP-WHEEL-CANDIDATE",
                "created_at": now,
                "decisions": [
                    {
                        "candidate_id": "CAND-WHEEL-001",
                        "decision": "selected",
                        "reason_code": "public_evidence_in_scope",
                        "explanation": "Public evidence is in scope.",
                        "priority": "high",
                    }
                ],
            },
        )
        screened_request = record(
            ArtifactSubmitRequest,
            request_id="REQ-WHEEL-SCREENED",
            run_id=run_id,
            artifact_id="screened_candidates",
            invocation_id=screening,
            input_path=screened_path.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
        )
        screened_request_path = write_json(
            screening_dir / "submit_request.json",
            screened_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "screened",
            screened_request_path.relative_to(workspace).as_posix(),
        )
        complete(
            "scout",
            [("candidate_claims", 1), ("screened_candidates", 1)],
        )

        claim_role = start(
            "REQ-WHEEL-INVOKE-CLAIMS",
            "claim-ledger",
            "claim-ledger",
        )
        claim_dir = workspace / "scratch" / claim_role
        claim_path = write_json(
            claim_dir / "claim_drafts.json",
            {
                "schema_version": "briefloop.claim_drafts_proposal.v2",
                "proposal_id": "PROP-WHEEL-CLAIMS",
                "run_id": run_id,
                "screened_candidates_proposal_id": "PROP-WHEEL-SCREENED",
                "created_at": now,
                "drafts": [
                    {
                        "draft_id": "DRAFT-WHEEL-001",
                        "statement": "ExampleCo opened a public pilot facility.",
                        "evidence_text": (
                            "ExampleCo opened a public pilot facility on 2026-07-14."
                        ),
                        "source_ids": ["SRC-WHEEL-001"],
                        "claim_type": "fact",
                    }
                ],
            },
        )
        claim_request = record(
            ArtifactSubmitRequest,
            request_id="REQ-WHEEL-CLAIM-DRAFTS",
            run_id=run_id,
            artifact_id="claim_drafts",
            invocation_id=claim_role,
            input_path=claim_path.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
        )
        claim_request_path = write_json(
            claim_dir / "submit_request.json",
            claim_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "claim-drafts",
            claim_request_path.relative_to(workspace).as_posix(),
        )
        core(
            "claim-freeze",
            record(
                ClaimFreezeRequest,
                request_id="REQ-WHEEL-FREEZE",
                run_id=run_id,
                claim_drafts_proposal_id="PROP-WHEEL-CLAIMS",
                expected_claim_drafts_artifact={
                    "artifact_id": "claim_drafts",
                    "revision": 1,
                },
                expected_store_revision=revision(),
                expected_ledger_revision=0,
            ),
        )
        complete(
            "claim-ledger",
            [("claim_drafts", 1), ("claim_ledger", 1)],
        )

        analyst = start("REQ-WHEEL-INVOKE-ANALYST", "analyst", "analyst")
        analyst_path = workspace / "scratch" / analyst / "analyst_draft_snapshot.md"
        analyst_path.parent.mkdir(parents=True, exist_ok=True)
        analyst_path.write_text(
            "# ExampleCo brief\n\nExampleCo opened a pilot. [src:CL-0001]\n",
            encoding="utf-8",
        )
        core(
            "artifact-submit",
            record(
                OwnedArtifactSubmitRequest,
                request_id="REQ-WHEEL-ANALYST-ARTIFACT",
                run_id=run_id,
                artifact_id="analyst_draft_snapshot",
                invocation_id=analyst,
                producer_tool_id="analyst-snapshot-v2",
                input_path=analyst_path.relative_to(workspace).as_posix(),
                expected_store_revision=revision(),
                expected_artifact_revision=0,
                expected_parent_artifact=None,
            ),
            scope=analyst,
        )
        complete("analyst", [("analyst_draft_snapshot", 1)])

        editor = start("REQ-WHEEL-INVOKE-EDITOR", "editor", "editor")
        brief_path = workspace / "scratch" / editor / "audited_brief.md"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(
            "# ExampleCo brief\n\n## Executive Summary\n\n"
            "ExampleCo opened a public pilot facility on 2026-07-14. "
            "[src:CL-0001]\n",
            encoding="utf-8",
        )
        core(
            "artifact-submit",
            record(
                OwnedArtifactSubmitRequest,
                request_id="REQ-WHEEL-EDITOR-ARTIFACT",
                run_id=run_id,
                artifact_id="audited_brief",
                invocation_id=editor,
                producer_tool_id=None,
                input_path=brief_path.relative_to(workspace).as_posix(),
                expected_store_revision=revision(),
                expected_artifact_revision=0,
                expected_parent_artifact={
                    "artifact_id": "analyst_draft_snapshot",
                    "revision": 1,
                },
            ),
            scope=editor,
        )
        complete(
            "editor",
            [("analyst_draft_snapshot", 1), ("audited_brief", 1)],
        )

        auditor = start("REQ-WHEEL-INVOKE-AUDITOR", "auditor", "auditor")
        audit_dir = workspace / "scratch" / auditor
        audit_path = write_json(
            audit_dir / "audit_proposal.json",
            {
                "schema_version": "briefloop.audit_proposal.v2",
                "proposal_id": "PROP-WHEEL-AUDIT",
                "run_id": run_id,
                "artifact_id": "audited_brief",
                "artifact_revision": 1,
                "decision": "pass",
                "created_at": now,
                "findings": [],
            },
        )
        audit_request = record(
            ArtifactSubmitRequest,
            request_id="REQ-WHEEL-AUDIT",
            run_id=run_id,
            artifact_id="audit_proposal",
            invocation_id=auditor,
            input_path=audit_path.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
        )
        audit_request_path = write_json(
            audit_dir / "submit_request.json",
            audit_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "audit",
            audit_request_path.relative_to(workspace).as_posix(),
        )
        core(
            "audit-promote",
            record(
                AuditPromotionRequest,
                request_id="REQ-WHEEL-AUDIT-PROMOTE",
                run_id=run_id,
                audit_proposal_id="PROP-WHEEL-AUDIT",
                expected_target_artifact={
                    "artifact_id": "audited_brief",
                    "revision": 1,
                },
                expected_audit_report_revision=0,
                expected_store_revision=revision(),
            ),
        )
        core(
            "gate-check",
            record(
                GateCheckRequest,
                request_id="REQ-WHEEL-GATE",
                run_id=run_id,
                stage_id="auditor",
                expected_store_revision=revision(),
                expected_report_artifact_revision=0,
                expected_input_artifacts=[
                    {"artifact_id": "claim_ledger", "revision": 1},
                    {"artifact_id": "audited_brief", "revision": 1},
                    {"artifact_id": "analyst_draft_snapshot", "revision": 1},
                    {"artifact_id": "screened_candidates", "revision": 1},
                    {"artifact_id": "candidate_claims", "revision": 1},
                ],
            ),
        )
        before_completion = snapshot()
        gate_ids = [
            item.evaluation_id
            for item in before_completion.gate_evaluations
            if item.gate_id in REQUIRED_AUDITOR_GATES
        ]
        auditor_artifacts = [
            ("claim_ledger", 1),
            ("audited_brief", 1),
            ("audit_report", 1),
            ("auditor_quality_gate_report", 1),
            ("analyst_draft_snapshot", 1),
        ]
        contaminated_workspace = workspace.with_name("wheel-core-contaminated")
        shutil.copytree(workspace, contaminated_workspace)
        complete("auditor", auditor_artifacts, gate_ids)

        clean = snapshot()
        clean_states = {item.stage_id: item.status for item in clean.stage_states}
        assert clean_states["auditor"] == "complete"
        assert clean_states["finalize"] == "ready"
        assert not clean.approvals
        assert not clean.deliveries
        assert not any(
            item.stage_id == "finalize" and item.transition_kind == "complete"
            for item in clean.stage_transitions
        )

        legacy = workspace / "output" / "intermediate" / "runtime_manifest.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy_bytes = b'{"runtime":"fake-legacy"}'
        legacy.write_bytes(legacy_bytes)
        legacy_revision = revision()
        legacy_states = {item.stage_id: item.status for item in snapshot().stage_states}
        assert legacy_revision == clean.store_revision
        assert legacy_states == clean_states
        assert legacy.read_bytes() == legacy_bytes
        control_paths = [
            "workflow_state.json",
            "artifact_registry.json",
            "event_log.jsonl",
            "finalize_report.json",
        ]
        assert not any(
            (workspace / "output" / "intermediate" / name).exists()
            for name in control_paths
        )

        contaminated_before = snapshot(contaminated_workspace)
        protected = next(
            item for item in contaminated_before.artifacts
            if item.artifact_id == "audited_brief"
        )
        (contaminated_workspace / protected.path).write_text(
            "MUTATED OUTSIDE CONTROLSTORE\n",
            encoding="utf-8",
        )
        blocked = complete(
            "auditor",
            auditor_artifacts,
            gate_ids,
            target=contaminated_workspace,
            expected="blocked",
        )
        assert blocked["error_code"] == "frozen_artifact_contaminated"
        contaminated = snapshot(contaminated_workspace)
        assert contaminated.store_revision == contaminated_before.store_revision + 1
        assert next(
            item for item in contaminated.stage_states if item.stage_id == "auditor"
        ).status == "ready"
        assert contaminated.run_integrity_records[-1].status == "contaminated"

        print(json.dumps({
            "auditor": clean_states["auditor"],
            "finalize": clean_states["finalize"],
            "claim_count": len(clean.claims),
            "gate_count": len(clean.gate_evaluations),
            "legacy_file_zero_truth": legacy_revision == clean.store_revision,
            "contamination_blocked": blocked["status"] == "blocked",
            "receipt_count": len(clean.transactions),
        }, sort_keys=True))
        """
    )
    script_path = tmp_path / "wheel_core_e2e.py"
    script_path.write_bytes(script.encode("utf-8"))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(installed)
    env["BRIEFLOOP_TEST_RETAINED_PUBLICATION_CAPABILITY"] = (
        "1" if supports_retained_directory_publication() else "0"
    )
    run = subprocess.run(
        _wheel_e2e_command(
            script_path=script_path,
            workspace=tmp_path / "wheel-core-workspace",
            installed=installed,
        ),
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    if sys.platform == "win32":
        assert run.stdout == (
            '{"package_imported": true, '
            '"publication_error": "checkout_publication_unsupported", '
            '"store_revision_unchanged": true}\n'
        )
    else:
        assert run.stdout == (
            '{"auditor": "complete", "claim_count": 1, '
            '"contamination_blocked": true, "finalize": "ready", '
            '"gate_count": 6, "legacy_file_zero_truth": true, '
            '"receipt_count": 28}\n'
        )
