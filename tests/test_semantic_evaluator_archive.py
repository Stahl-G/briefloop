"""Adversarial append-only publication and replay verification tests."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

import multi_agent_brief.semantic_evaluator.archive as archive_module
from multi_agent_brief.semantic_evaluator.archive import trial_archive_path
from multi_agent_brief.semantic_evaluator.runner import PROFILE_ID, run_shadow
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
)


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"
FIXED_TIME = "2026-07-17T00:00:00Z"


def _invocation(tmp_path: Path, trial_id: str = "trial-archive-v1"):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name in ("report.md", "bounded_context.json", "instrument.json"):
        shutil.copyfile(FIXTURES / name, inputs / name)
    return {
        "report": inputs / "report.md",
        "bounded_context": inputs / "bounded_context.json",
        "profile": PROFILE_ID,
        "instrument": inputs / "instrument.json",
        "trial_id": trial_id,
        "archive_root": tmp_path / "archives",
        "clock": lambda: FIXED_TIME,
        "sleep": lambda _seconds: None,
    }


def test_trial_path_is_hash_derived_only_from_strict_trial_identity(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "archives").resolve()
    first = trial_archive_path(root, "trial-archive-v1")
    second = trial_archive_path(root, "trial-archive-v1")
    other = trial_archive_path(root, "trial-archive-v2")
    assert first == second
    assert first != other
    assert first.parent == root / "semantic-evaluator" / "v0.1" / "trials"
    assert first.name.startswith("trial-")
    assert "trial-archive-v1" not in first.name


def test_incomplete_claimed_archive_is_never_repaired_or_overwritten(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    final = trial_archive_path(Path(invocation["archive_root"]), invocation["trial_id"])
    final.mkdir(parents=True)
    (final / "request.json").write_text("{}", encoding="utf-8")
    result = run_shadow(**invocation)
    assert result.reason_codes == ("shadow_archive_incomplete",)
    assert (final / "request.json").read_text(encoding="utf-8") == "{}"
    assert not (final / "COMPLETE").exists()


def test_existing_winner_is_verified_without_merge_or_overwrite(tmp_path: Path) -> None:
    invocation = _invocation(tmp_path)
    first = run_shadow(**invocation)
    assert first.ok is True
    archive = Path(first.archive_path or "")
    before = {
        item.relative_to(archive).as_posix(): item.read_bytes()
        for item in archive.rglob("*")
        if item.is_file()
    }
    second = run_shadow(**invocation)
    after = {
        item.relative_to(archive).as_posix(): item.read_bytes()
        for item in archive.rglob("*")
        if item.is_file()
    }
    assert second.replayed is True
    assert second.receipt_id == first.receipt_id
    assert after == before


def test_extra_member_and_symlink_member_fail_closed_before_adapter(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    first = run_shadow(**invocation)
    assert first.ok is True
    archive = Path(first.archive_path or "")
    (archive / "extra.txt").write_text("extra", encoding="utf-8")
    touched = False

    def forbidden(_execution):
        nonlocal touched
        touched = True
        raise AssertionError

    extra = run_shadow(**invocation, adapter_factory=forbidden)
    assert extra.reason_codes == ("shadow_archive_invalid",)
    assert touched is False
    (archive / "extra.txt").unlink()

    target = next((archive / "attempts").glob("*/*/output.txt"))
    original = target.read_bytes()
    target.unlink()
    try:
        target.symlink_to(archive / "request.json")
    except OSError:
        pytest.skip("symlinks unavailable")
    symlinked = run_shadow(**invocation, adapter_factory=forbidden)
    assert symlinked.reason_codes == ("shadow_archive_invalid",)
    assert touched is False
    target.unlink()
    target.write_bytes(original)


def _rehash_control_chain(archive: Path, changed_path: str) -> None:
    manifest_path = archive / "archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = (archive / changed_path).read_bytes()
    for member in manifest["payload_members"]:
        if member["path"] == changed_path:
            member["size_bytes"] = len(changed)
            member["sha256"] = sha256_bytes(changed)
            break
    manifest["aggregate_payload_sha256"] = canonical_sha256(manifest["payload_members"])
    manifest_without_hash = {
        key: value
        for key, value in manifest.items()
        if key != "archive_manifest_sha256"
    }
    manifest["archive_manifest_sha256"] = canonical_sha256(manifest_without_hash)
    manifest_raw = canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_raw)

    receipt_path = archive / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["archive_manifest_sha256"] = manifest["archive_manifest_sha256"]
    receipt_without_hash = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt_without_hash)
    receipt_raw = canonical_json_bytes(receipt)
    receipt_path.write_bytes(receipt_raw)
    (archive / "COMPLETE").write_bytes(
        (sha256_bytes(receipt_raw) + "\n").encode("ascii")
    )


def test_self_consistent_rehash_of_semantic_projection_still_fails_replay(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    first = run_shadow(**invocation)
    assert first.ok is True
    archive = Path(first.archive_path or "")
    presentation_path = archive / "presentation_actual.json"
    presentation = json.loads(presentation_path.read_text(encoding="utf-8"))
    presentation["disclaimer"] = "被手工修改的合成展示。"
    without_hash = {
        key: value
        for key, value in presentation.items()
        if key != "presentation_sha256"
    }
    presentation["presentation_sha256"] = canonical_sha256(without_hash)
    presentation_path.write_bytes(canonical_json_bytes(presentation))
    _rehash_control_chain(archive, "presentation_actual.json")
    result = run_shadow(**invocation)
    assert result.reason_codes == ("shadow_archive_invalid",)


def test_publication_failure_before_claim_leaves_no_final_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invocation = _invocation(tmp_path)

    def fail_before_claim(_path, _raw):
        raise archive_module.SemanticEvaluatorError("shadow_archive_publish_failed")

    monkeypatch.setattr(archive_module, "_write_exclusive", fail_before_claim)
    result = run_shadow(**invocation)
    assert result.reason_codes == ("shadow_archive_publish_failed",)
    final = trial_archive_path(Path(invocation["archive_root"]), invocation["trial_id"])
    assert not final.exists()


def test_publication_failure_after_claim_remains_incomplete_and_future_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invocation = _invocation(tmp_path)
    original = archive_module._write_exclusive

    def fail_final_receipt(path: Path, raw: bytes):
        if path.name == "receipt.json" and not any(
            part.startswith(".staging-") for part in path.parts
        ):
            raise archive_module.SemanticEvaluatorError("shadow_archive_publish_failed")
        return original(path, raw)

    monkeypatch.setattr(archive_module, "_write_exclusive", fail_final_receipt)
    first = run_shadow(**invocation)
    assert first.reason_codes == ("shadow_archive_publish_failed",)
    final = trial_archive_path(Path(invocation["archive_root"]), invocation["trial_id"])
    assert final.is_dir()
    assert not (final / "COMPLETE").exists()
    monkeypatch.setattr(archive_module, "_write_exclusive", original)
    second = run_shadow(**invocation)
    assert second.reason_codes == ("shadow_archive_incomplete",)


def test_complete_write_or_reopen_failure_never_reports_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invocation = _invocation(tmp_path)
    original = archive_module._write_exclusive

    def fail_final_complete(path: Path, raw: bytes):
        if path.name == "COMPLETE" and not any(
            part.startswith(".staging-") for part in path.parts
        ):
            raise archive_module.SemanticEvaluatorError("shadow_archive_publish_failed")
        return original(path, raw)

    monkeypatch.setattr(archive_module, "_write_exclusive", fail_final_complete)
    result = run_shadow(**invocation)
    assert result.ok is False
    assert result.archive_complete is False
    assert result.reason_codes == ("shadow_archive_publish_failed",)
    final = trial_archive_path(Path(invocation["archive_root"]), invocation["trial_id"])
    assert final.is_dir()
    assert not (final / "COMPLETE").exists()


def test_staging_cleanup_failure_does_not_change_verified_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invocation = _invocation(tmp_path)
    monkeypatch.setattr(
        archive_module.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError()),
    )
    result = run_shadow(**invocation)
    assert result.ok is True
    assert result.archive_complete is True
    assert result.receipt_id is not None
