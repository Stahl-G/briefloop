"""Deterministic human-facing projections of verified LAJ shadow archives.

This module is deliberately outside every BriefLoop runtime/control surface.  It
reads one immutable experimental archive and emits advisory presentation files;
it cannot change workflow legality or delivery truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import ClassVar, Literal

from pydantic import TypeAdapter, model_validator

from multi_agent_brief.contracts.v2 import (
    CleanText,
    ContractId,
    NonNegativeInt,
    Sha256,
    StrictModel,
)
from multi_agent_brief.semantic_evaluator.archive import verify_shadow_archive
from multi_agent_brief.semantic_evaluator.contracts import FindingProposal, RunStatus
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
)


LAJ_READER_SCHEMA_ID = "briefloop.semantic_evaluator.reader_view.v1"
LAJ_READER_FILENAMES = ("laj.html", "laj.json", "laj.md")
LAJ_READER_BOUNDARY = (
    "Experimental AI assessment. Advisory only. Not a Gate, delivery decision, "
    "or proof of correctness."
)
ReaderStatus = Literal[
    "available",
    "abstained",
    "invalid",
    "not_available",
    "stale",
    "unavailable",
]
_SHA256 = TypeAdapter(Sha256)
_OUTPUT_DIRECTORY_PATTERN = re.compile(
    r"^laj-advisory-[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"
)
_WORKSPACE_CONTROL_MARKERS = (
    "artifact_registry.json",
    "control_store.db",
    "control_store.sqlite3",
    "event_log.jsonl",
    "runtime_manifest.json",
    "workflow_state.json",
)
_WORKSPACE_REQUIRED_MARKERS = ("config.yaml", "sources.yaml", "user.md")


class LajReaderBinding(StrictModel):
    artifact_id: ContractId
    report_sha256: Sha256
    trial_id: ContractId
    shadow_receipt_id: ContractId
    instrument_sha256: Sha256
    execution_sha256: Sha256
    execution_origin: ContractId
    model_id: ContractId
    model_version: CleanText
    archive_manifest_sha256: Sha256
    presentation_sha256: Sha256


class LajReaderView(StrictModel):
    schema_id: ClassVar[str] = LAJ_READER_SCHEMA_ID
    schema_version: Literal[LAJ_READER_SCHEMA_ID]
    status: ReaderStatus
    boundary: Literal[
        "Experimental AI assessment. Advisory only. Not a Gate, delivery decision, or proof of correctness."
    ]
    advisory_only: Literal[True]
    shadow_only: Literal[True]
    runtime_authority: Literal[False]
    authority_effect: Literal["none"]
    archive_verified: bool
    binding: LajReaderBinding | None
    run_status: RunStatus | None
    validation_status: Literal["accepted", "rejected", "incomplete"] | None
    reason_codes: list[ContractId]
    assessed_unit_count: NonNegativeInt
    finding_count: NonNegativeInt
    withheld_finding_count: NonNegativeInt
    abstention_count: NonNegativeInt
    findings: list[FindingProposal]
    disclaimer: CleanText
    view_sha256: Sha256

    @model_validator(mode="after")
    def validate_advisory_projection(self) -> "LajReaderView":
        if self.reason_codes != sorted(set(self.reason_codes)):
            raise ValueError("reader reason codes must be sorted and unique")
        if self.finding_count != len(self.findings):
            raise ValueError("reader finding count mismatch")
        if self.status != "available" and self.findings:
            raise ValueError("non-available reader views cannot display findings")
        if self.archive_verified:
            if (
                self.binding is None
                or self.run_status is None
                or self.validation_status is None
            ):
                raise ValueError("verified reader views require an exact binding")
        elif any(
            item is not None
            for item in (self.binding, self.run_status, self.validation_status)
        ):
            raise ValueError("unverified reader views cannot carry archive facts")
        if self.status in {"available", "abstained"} and (
            self.run_status != "completed" or self.validation_status != "accepted"
        ):
            raise ValueError("displayable reader status requires accepted completion")
        expected = canonical_sha256(
            self.model_dump(
                mode="json",
                exclude={"view_sha256"},
                warnings="error",
            )
        )
        if self.view_sha256 != expected:
            raise ValueError("reader view hash mismatch")
        return self


@dataclass(frozen=True)
class LajReaderArtifacts:
    output_dir: Path
    view: LajReaderView
    json_sha256: str
    markdown_sha256: str
    html_sha256: str


def _finalize_view(payload: dict[str, object]) -> LajReaderView:
    return LajReaderView.model_validate(
        {**payload, "view_sha256": canonical_sha256(payload)}
    )


def _empty_view(*, status: ReaderStatus, reason_code: str) -> LajReaderView:
    payload: dict[str, object] = {
        "schema_version": LAJ_READER_SCHEMA_ID,
        "status": status,
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": False,
        "binding": None,
        "run_status": None,
        "validation_status": None,
        "reason_codes": [reason_code],
        "assessed_unit_count": 0,
        "finding_count": 0,
        "withheld_finding_count": 0,
        "abstention_count": 0,
        "findings": [],
        "disclaimer": (
            "Experimental advisory assessment is not available. No workflow, "
            "Gate, finalization, delivery, repair, approval, or next-action effect."
        ),
    }
    return _finalize_view(payload)


def build_laj_reader_view(
    archive_path: str | Path,
    *,
    expected_report_sha256: str | None = None,
) -> LajReaderView:
    """Build one advisory view; malformed/missing archives fail closed to no advice."""

    expected_sha: str | None = None
    if expected_report_sha256 is not None:
        try:
            expected_sha = _SHA256.validate_python(expected_report_sha256)
        except Exception:
            raise SemanticEvaluatorError("shadow_request_invalid") from None
    try:
        verified = verify_shadow_archive(Path(archive_path))
    except SemanticEvaluatorError as exc:
        status: ReaderStatus = (
            "not_available"
            if exc.reason_code == "shadow_archive_incomplete"
            else "invalid"
        )
        return _empty_view(status=status, reason_code=exc.reason_code)

    presentation = verified.presentation
    witness = verified.witness
    binding = LajReaderBinding(
        artifact_id=verified.request.artifact_id,
        report_sha256=witness.input_binding.report_sha256,
        trial_id=verified.request.trial_id,
        shadow_receipt_id=verified.receipt.receipt_id,
        instrument_sha256=verified.request.instrument_sha256,
        execution_sha256=verified.request.execution_sha256,
        execution_origin=verified.execution_manifest.execution_origin,
        model_id=witness.instrument_config.model_id,
        model_version=witness.instrument_config.model_version,
        archive_manifest_sha256=verified.archive_manifest.archive_manifest_sha256,
        presentation_sha256=presentation.presentation_sha256,
    )
    status: ReaderStatus
    reasons = list(verified.reason_codes)
    if expected_sha is not None and expected_sha != binding.report_sha256:
        status = "stale"
        reasons = sorted(set([*reasons, "report_binding_stale"]))
    elif not verified.ok:
        status = (
            "invalid"
            if verified.receipt.run_status
            in {"parser_failed", "security_failed", "validation_failed"}
            else "unavailable"
        )
    elif (
        presentation.assessed_unit_count > 0
        and presentation.abstention_count == presentation.assessed_unit_count
        and presentation.finding_count == 0
    ):
        status = "abstained"
        reasons = sorted(set([*reasons, "assessment_abstained"]))
    else:
        status = "available"

    findings = (
        list(presentation.additional_semantic_findings) if status == "available" else []
    )
    disclaimer = (
        presentation.disclaimer
        if status == "available"
        else (
            "Experimental advisory assessment is unavailable, invalid, stale, or "
            "abstained. No workflow, Gate, finalization, delivery, repair, approval, "
            "or next-action effect."
        )
    )
    payload = {
        "schema_version": LAJ_READER_SCHEMA_ID,
        "status": status,
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": True,
        "binding": binding.model_dump(mode="json", warnings="error"),
        "run_status": verified.receipt.run_status,
        "validation_status": verified.receipt.validation_status,
        "reason_codes": reasons,
        "assessed_unit_count": presentation.assessed_unit_count,
        "finding_count": len(findings),
        "withheld_finding_count": (
            presentation.withheld_finding_count
            + presentation.finding_count
            - len(findings)
        ),
        "abstention_count": presentation.abstention_count,
        "findings": [
            item.model_dump(mode="json", warnings="error") for item in findings
        ],
        "disclaimer": disclaimer,
    }
    return _finalize_view(payload)


def _markdown_text(value: object) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    text = escape(text, quote=False)
    for marker in ("\\", "`", "*", "_", "|", "#", "[", "]", "(", ")", "!", ">"):
        text = text.replace(marker, f"\\{marker}")
    return text


def render_laj_reader_json(view: LajReaderView) -> bytes:
    return canonical_json_bytes(view) + b"\n"


def render_laj_reader_markdown(view: LajReaderView) -> bytes:
    lines = [
        "# Experimental AI assessment",
        "",
        "> Advisory only · Offline shadow · Not a Gate, delivery decision, or proof of correctness",
        "",
        f"- Status: `{view.status}`",
        f"- Assessed units: `{view.assessed_unit_count}`",
        f"- Candidate findings: `{view.finding_count}`",
        f"- Abstentions: `{view.abstention_count}`",
        f"- Runtime authority: `none`",
        f"- View SHA-256: `{view.view_sha256}`",
        "",
        _markdown_text(view.disclaimer),
    ]
    if view.binding is not None:
        lines.extend(
            [
                "",
                "## Evidence binding",
                "",
                f"- Report SHA-256: `{view.binding.report_sha256}`",
                f"- Instrument SHA-256: `{view.binding.instrument_sha256}`",
                f"- Model: `{_markdown_text(view.binding.model_id)}` / `{_markdown_text(view.binding.model_version)}`",
                f"- Shadow receipt: `{_markdown_text(view.binding.shadow_receipt_id)}`",
            ]
        )
    lines.extend(["", "## Candidate findings", ""])
    if not view.findings:
        lines.append("No displayable candidate finding is available for this view.")
    for index, finding in enumerate(view.findings, start=1):
        lines.extend(
            [
                f"### {index}. {_markdown_text(finding.dimension_id)} · {_markdown_text(finding.severity)}",
                "",
                f"- Observation: {_markdown_text(finding.observation)}",
                f"- Rationale: {_markdown_text(finding.rationale)}",
                f"- Severity basis: {_markdown_text(finding.severity_basis)}",
                f"- Recommended human action: `{_markdown_text(finding.recommended_human_action)}`",
                "- Bound spans: "
                + ", ".join(
                    f"`{_markdown_text(span.block_id)}:{span.start_char}-{span.end_char}`"
                    for span in finding.report_spans
                ),
                "",
            ]
        )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def render_laj_reader_html(view: LajReaderView) -> bytes:
    binding = ""
    if view.binding is not None:
        binding = f"""
        <section class="card">
          <h2>Evidence binding</h2>
          <dl>
            <dt>Report SHA-256</dt><dd><code>{escape(view.binding.report_sha256)}</code></dd>
            <dt>Instrument SHA-256</dt><dd><code>{escape(view.binding.instrument_sha256)}</code></dd>
            <dt>Model</dt><dd>{escape(view.binding.model_id)} / {escape(view.binding.model_version)}</dd>
            <dt>Shadow receipt</dt><dd><code>{escape(view.binding.shadow_receipt_id)}</code></dd>
          </dl>
        </section>"""
    findings = "<p>No displayable candidate finding is available for this view.</p>"
    if view.findings:
        cards = []
        for finding in view.findings:
            spans = ", ".join(
                f"{escape(span.block_id)}:{span.start_char}-{span.end_char}"
                for span in finding.report_spans
            )
            cards.append(
                f"""<article class="finding">
                <div class="finding-head"><span>{escape(finding.dimension_id)}</span><strong>{escape(finding.severity)}</strong></div>
                <h3>{escape(finding.observation)}</h3>
                <p>{escape(finding.rationale)}</p>
                <p><b>Severity basis:</b> {escape(finding.severity_basis)}</p>
                <p><b>Recommended human action:</b> {escape(finding.recommended_human_action)}</p>
                <p class="muted"><b>Bound spans:</b> {spans}</p>
                </article>"""
            )
        findings = "".join(cards)
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Experimental AI assessment</title>
<style>
:root{{--ink:#172033;--muted:#657087;--paper:#f4f7fb;--card:#fff;--line:#d9e0ea;--accent:#3157d5;--warn:#8a5a00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:980px;margin:0 auto;padding:48px 22px 72px}}h1{{font-size:34px;margin:.2rem 0}}h2{{margin-top:0}}code{{overflow-wrap:anywhere}}
.eyebrow{{color:var(--accent);font-weight:750;letter-spacing:.08em;text-transform:uppercase}}.boundary{{border-left:4px solid var(--warn);background:#fff8e8;padding:14px 18px;margin:22px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin:22px 0}}.metric,.card,.finding{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}}
.metric b{{display:block;font-size:24px}}.metric span,.muted{{color:var(--muted)}}dl{{display:grid;grid-template-columns:150px 1fr;gap:8px 16px}}dt{{font-weight:700}}dd{{margin:0;overflow-wrap:anywhere}}
.finding{{margin:12px 0}}.finding-head{{display:flex;justify-content:space-between;color:var(--accent)}}.finding h3{{font-size:18px}}
@media(max-width:600px){{dl{{grid-template-columns:1fr}}main{{padding-top:28px}}}}
</style></head><body><main>
<p class="eyebrow">Experimental · Offline shadow · Advisory only</p>
<h1>AI assessment second opinion</h1>
<div class="boundary">Not a Gate, delivery decision, or proof of correctness. Runtime authority: none.</div>
<div class="grid"><div class="metric"><span>Status</span><b>{escape(view.status)}</b></div><div class="metric"><span>Assessed units</span><b>{view.assessed_unit_count}</b></div><div class="metric"><span>Candidate findings</span><b>{view.finding_count}</b></div><div class="metric"><span>Abstentions</span><b>{view.abstention_count}</b></div></div>
<section class="card"><h2>Assessment note</h2><p>{escape(view.disclaimer)}</p><p class="muted">View SHA-256: <code>{view.view_sha256}</code></p></section>
{binding}
<section><h2>Candidate findings</h2>{findings}</section>
</main></body></html>
"""
    return html.encode("utf-8")


def _absolute_lexical_path(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path.expanduser())))
    except (OSError, RuntimeError, TypeError, ValueError):
        raise SemanticEvaluatorError("laj_presentation_write_failed") from None


def _assert_real_directory_chain(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except (OSError, RuntimeError):
            raise SemanticEvaluatorError("laj_presentation_write_failed") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise SemanticEvaluatorError("laj_presentation_write_failed")
    try:
        metadata = path.lstat()
    except (OSError, RuntimeError):
        raise SemanticEvaluatorError("laj_presentation_write_failed") from None
    if not stat.S_ISDIR(metadata.st_mode):
        raise SemanticEvaluatorError("laj_presentation_write_failed")


def _exists_without_following(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError):
        raise SemanticEvaluatorError("laj_presentation_write_failed") from None
    return True


def _is_briefloop_workspace(path: Path) -> bool:
    if all(
        _exists_without_following(path / name) for name in _WORKSPACE_REQUIRED_MARKERS
    ):
        return True
    if any(
        _exists_without_following(path / name) for name in _WORKSPACE_CONTROL_MARKERS
    ):
        return True
    return _exists_without_following(path / ".briefloop" / "control_store.sqlite3")


def _presentation_destination(
    *,
    archive_path: str | Path,
    output_dir: str | Path,
) -> Path:
    destination = _absolute_lexical_path(Path(output_dir))
    if _OUTPUT_DIRECTORY_PATTERN.fullmatch(destination.name) is None:
        raise SemanticEvaluatorError("laj_presentation_write_failed")
    parent = destination.parent
    _assert_real_directory_chain(parent)
    if _exists_without_following(destination):
        raise SemanticEvaluatorError("laj_presentation_write_failed")
    if any(
        _is_briefloop_workspace(candidate) for candidate in (parent, *parent.parents)
    ):
        raise SemanticEvaluatorError("laj_presentation_write_failed")

    archive = _absolute_lexical_path(Path(archive_path))
    try:
        archive_resolved = archive.resolve(strict=True)
        parent_resolved = parent.resolve(strict=True)
    except FileNotFoundError:
        archive_resolved = None
    except (OSError, RuntimeError):
        raise SemanticEvaluatorError("laj_presentation_write_failed") from None
    if archive_resolved is not None:
        resolved_destination = parent_resolved / destination.name
        if (
            resolved_destination == archive_resolved
            or archive_resolved in resolved_destination.parents
        ):
            raise SemanticEvaluatorError("laj_presentation_write_failed")
    return destination


def write_laj_reader_artifacts(
    *,
    archive_path: str | Path,
    output_dir: str | Path,
    expected_report_sha256: str | None = None,
) -> LajReaderArtifacts:
    """Write one new immutable advisory presentation directory."""

    destination = _presentation_destination(
        archive_path=archive_path,
        output_dir=output_dir,
    )
    parent = destination.parent

    view = build_laj_reader_view(
        archive_path,
        expected_report_sha256=expected_report_sha256,
    )
    payloads = {
        "laj.html": render_laj_reader_html(view),
        "laj.json": render_laj_reader_json(view),
        "laj.md": render_laj_reader_markdown(view),
    }
    staging: Path | None = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=".laj-reader-", dir=parent))
        for name in LAJ_READER_FILENAMES:
            (staging / name).write_bytes(payloads[name])
        os.replace(staging, destination)
        staging = None
    except OSError:
        raise SemanticEvaluatorError("laj_presentation_write_failed") from None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return LajReaderArtifacts(
        output_dir=destination,
        view=view,
        json_sha256=sha256_bytes(payloads["laj.json"]),
        markdown_sha256=sha256_bytes(payloads["laj.md"]),
        html_sha256=sha256_bytes(payloads["laj.html"]),
    )


__all__ = [
    "LAJ_READER_BOUNDARY",
    "LAJ_READER_FILENAMES",
    "LAJ_READER_SCHEMA_ID",
    "LajReaderArtifacts",
    "LajReaderBinding",
    "LajReaderView",
    "build_laj_reader_view",
    "render_laj_reader_html",
    "render_laj_reader_json",
    "render_laj_reader_markdown",
    "write_laj_reader_artifacts",
]
