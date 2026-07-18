"""One-command public-safe demonstration of the isolated LAJ product path."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_FILENAMES,
    write_laj_reader_artifacts,
)
from multi_agent_brief.semantic_evaluator.runner import PROFILE_ID, run_shadow
from multi_agent_brief.semantic_evaluator.serialization import sha256_bytes


PUBLIC_SAFE_DEMO_TRIAL_ID = "trial-public-safe-laj-demo-v1"
_FIXTURE_PACKAGE = "multi_agent_brief.semantic_evaluator"
_FIXTURE_PARTS = ("fixtures", "synthetic_shadow_v1")


@dataclass(frozen=True)
class PublicSafeLajDemoResult:
    ok: bool
    replayed: bool
    archive_complete: bool
    presentation_available: bool
    receipt_id: str | None
    reader_status: str | None
    finding_count: int
    reason_codes: tuple[str, ...]
    output_files: tuple[str, ...]
    view_sha256: str | None
    execution_origin: str | None
    qualification_class: str
    qualification_eligible: bool
    runtime_authority: bool


def _fixture(name: str):
    return resources.files(_FIXTURE_PACKAGE).joinpath(*_FIXTURE_PARTS, name)


def run_public_safe_laj_demo(
    *,
    archive_root: str | Path,
    output_dir: str | Path,
) -> PublicSafeLajDemoResult:
    """Run/replay the packaged synthetic trial and render standalone artifacts."""

    with ExitStack() as stack:
        report_path = stack.enter_context(resources.as_file(_fixture("report.md")))
        context_path = stack.enter_context(
            resources.as_file(_fixture("bounded_context.json"))
        )
        instrument_path = stack.enter_context(
            resources.as_file(_fixture("instrument.json"))
        )
        try:
            report_sha256 = sha256_bytes(report_path.read_bytes())
        except (OSError, RuntimeError):
            raise SemanticEvaluatorError("shadow_adapter_unavailable") from None
        shadow = run_shadow(
            report=report_path,
            bounded_context=context_path,
            profile=PROFILE_ID,
            instrument=instrument_path,
            trial_id=PUBLIC_SAFE_DEMO_TRIAL_ID,
            archive_root=archive_root,
        )

    if not shadow.ok or shadow.archive_path is None:
        return PublicSafeLajDemoResult(
            ok=False,
            replayed=shadow.replayed,
            archive_complete=shadow.archive_complete,
            presentation_available=False,
            receipt_id=shadow.receipt_id,
            reader_status=None,
            finding_count=0,
            reason_codes=shadow.reason_codes,
            output_files=(),
            view_sha256=None,
            execution_origin=shadow.execution_origin,
            qualification_class="synthetic_demo_only",
            qualification_eligible=False,
            runtime_authority=False,
        )

    try:
        reader = write_laj_reader_artifacts(
            archive_path=shadow.archive_path,
            output_dir=output_dir,
            expected_report_sha256=report_sha256,
        )
    except SemanticEvaluatorError as exc:
        return PublicSafeLajDemoResult(
            ok=False,
            replayed=shadow.replayed,
            archive_complete=True,
            presentation_available=False,
            receipt_id=shadow.receipt_id,
            reader_status="unavailable",
            finding_count=0,
            reason_codes=(exc.reason_code,),
            output_files=(),
            view_sha256=None,
            execution_origin=shadow.execution_origin,
            qualification_class="synthetic_demo_only",
            qualification_eligible=False,
            runtime_authority=False,
        )

    return PublicSafeLajDemoResult(
        ok=True,
        replayed=shadow.replayed,
        archive_complete=True,
        presentation_available=True,
        receipt_id=shadow.receipt_id,
        reader_status=reader.view.status,
        finding_count=reader.view.finding_count,
        reason_codes=(),
        output_files=LAJ_READER_FILENAMES,
        view_sha256=reader.view.view_sha256,
        execution_origin=shadow.execution_origin,
        qualification_class="synthetic_demo_only",
        qualification_eligible=False,
        runtime_authority=False,
    )


__all__ = [
    "PUBLIC_SAFE_DEMO_TRIAL_ID",
    "PublicSafeLajDemoResult",
    "run_public_safe_laj_demo",
]
