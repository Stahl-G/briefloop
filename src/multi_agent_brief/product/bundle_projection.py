"""Product-layer delivery/audit bundle projection.

This module classifies already-finalized workspace artifacts. It does not move
files, render templates, deliver reports, or approve publication.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import secrets
import stat
import zipfile
from pathlib import Path
from typing import Any, Callable

from multi_agent_brief.outputs.reader_final_gate import (
    detect_reader_residue,
    detect_reader_residue_in_docx,
)
from multi_agent_brief.outputs.finalize import (
    interpret_finalize_audit_binding,
    require_finalize_audit_binding_pass,
)
from multi_agent_brief.product.citation_profile import (
    DEFAULT_CITATION_PROFILE,
    citation_profile_report,
    normalize_citation_profile,
    validate_citation_profile_report,
)
from multi_agent_brief.product.quality_panel import (
    QualityPanelError,
    render_quality_panel_html,
    render_quality_summary,
    validate_quality_panel_html,
    validate_quality_panel_payload,
    validate_quality_summary_markdown,
)
from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication as _supports_safe_bundle_publication,
)
from multi_agent_brief.product.report_spec import ReportSpecLoadError, load_report_spec
from multi_agent_brief.product.template_registry import ReportTemplateRegistry
from multi_agent_brief.product.workspace_hygiene import classify_workspace_member

REPORT_BUNDLE_MANIFEST_SCHEMA_VERSION = "briefloop.report_bundle_manifest.v1"
_ASCII_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DELIVERY_BUNDLE_README_MEMBER = "delivery/_BUNDLE_README.md"
_AUDIT_BUNDLE_README_MEMBER = "audit/_BUNDLE_README.md"
_DELIVERY_BUNDLE_README = """# BriefLoop Delivery Bundle

Open the files in this bundle for the reader-facing report.

- `brief.md` is the local Markdown delivery when present.
- DOCX or other configured delivery files are reader-facing copies of the same finalized report surface.
- Audit/control artifacts are intentionally excluded from this bundle.

This bundle does not prove semantic truth, approve publication, or replace human review before sending.
For claim, source, gate, event, and quality traces, open the separate audit bundle.
"""
_AUDIT_BUNDLE_README = """# BriefLoop Audit Bundle

Open this bundle when a reviewer asks where a claim, warning, gate result, or delivery decision came from.

Useful starting points when present:

- `output/intermediate/quality_summary.md` for a compact quality summary.
- `output/intermediate/quality_panel.html` for a static inspection panel.
- `output/intermediate/claim_ledger.json` for recorded claims.
- `output/source_appendix.md` and `output/source_appendix_trace.md` for source trail review.
- `output/intermediate/audit_report.json`, gate reports, workflow state, runtime manifest, and event log for control records.

This bundle is not reader delivery, semantic proof, delivery approval, or release authority.
Do not edit these control files in place to change a run outcome.
"""


class ReportBundleProjectionError(Exception):
    """Raised when a bundle projection cannot be built safely."""


@dataclass(frozen=True)
class _LeafObservation:
    kind: str
    identity: tuple[int, int] | None = None
    sha256: str | None = None
    size: int | None = None


class _ProjectionParent:
    """One verified parent retained across staging and relative publication."""

    def __init__(
        self,
        *,
        workspace: Path,
        path: Path,
        root_identity: tuple[int, int],
        chain: tuple[tuple[str, tuple[int, int]], ...],
        root_fd: int,
        parent_fd: int,
    ) -> None:
        self.workspace = workspace
        self.path = path
        self.root_identity = root_identity
        self.chain = chain
        self.root_fd = root_fd
        self.parent_fd = parent_fd

    @classmethod
    def open(cls, workspace: Path, parent: Path) -> "_ProjectionParent":
        if not _supports_safe_bundle_publication():
            raise ReportBundleProjectionError(
                "bundle_projection_publication_unsupported"
            )
        workspace = workspace.resolve(strict=True)
        parent = Path(os.path.abspath(parent))
        try:
            relative = parent.relative_to(workspace)
        except ValueError as exc:
            raise ReportBundleProjectionError(
                "bundle projection parent must stay inside the workspace."
            ) from exc
        return cls._open_retained(workspace, parent, relative.parts)

    @classmethod
    def _open_retained(
        cls,
        workspace: Path,
        parent: Path,
        parts: tuple[str, ...],
    ) -> "_ProjectionParent":
        root_fd = -1
        current_fd = -1
        try:
            root_fd = os.open(
                workspace,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            root_info = os.fstat(root_fd)
            if not stat.S_ISDIR(root_info.st_mode):
                raise OSError("workspace root is not a directory")
            current_fd = os.dup(root_fd)
            chain: list[tuple[str, tuple[int, int]]] = []
            for part in parts:
                try:
                    os.mkdir(part, 0o755, dir_fd=current_fd)
                except FileExistsError:
                    pass
                observed = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
                if not stat.S_ISDIR(observed.st_mode):
                    raise OSError("bundle projection parent is not a directory")
                child_fd = os.open(
                    part,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=current_fd,
                )
                opened = os.fstat(child_fd)
                identity = (observed.st_dev, observed.st_ino)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != identity
                ):
                    os.close(child_fd)
                    raise OSError("bundle projection parent changed")
                os.close(current_fd)
                current_fd = child_fd
                chain.append((part, identity))
            return cls(
                workspace=workspace,
                path=parent,
                root_identity=(root_info.st_dev, root_info.st_ino),
                chain=tuple(chain),
                root_fd=root_fd,
                parent_fd=current_fd,
            )
        except OSError as exc:
            if current_fd >= 0:
                os.close(current_fd)
            if root_fd >= 0:
                os.close(root_fd)
            raise ReportBundleProjectionError(
                "bundle projection parent is unavailable."
            ) from exc

    def close(self) -> None:
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    def __enter__(self) -> "_ProjectionParent":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def reverify(self) -> None:
        try:
            root_live = self.workspace.lstat()
            if (
                not stat.S_ISDIR(root_live.st_mode)
                or self.workspace.is_symlink()
                or (root_live.st_dev, root_live.st_ino) != self.root_identity
            ):
                raise OSError("workspace root changed")
            current_fd = os.dup(self.root_fd)
            try:
                for part, identity in self.chain:
                    child_fd = os.open(
                        part,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=current_fd,
                    )
                    opened = os.fstat(child_fd)
                    os.close(current_fd)
                    current_fd = child_fd
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != identity
                    ):
                        raise OSError("bundle projection parent changed")
            finally:
                os.close(current_fd)
        except OSError as exc:
            raise ReportBundleProjectionError(
                "bundle projection parent changed."
            ) from exc

    def observe(self, leaf: str) -> _LeafObservation:
        _validate_projection_leaf(leaf)
        try:
            observed = os.stat(
                leaf,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISREG(observed.st_mode):
                return _LeafObservation("unsafe")
            fd = os.open(
                leaf,
                os.O_RDONLY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=self.parent_fd,
            )
        except FileNotFoundError:
            return _LeafObservation("absent")
        except OSError as exc:
            raise ReportBundleProjectionError(
                "bundle projection target is unreadable."
            ) from exc
        try:
            opened = os.fstat(fd)
            payload = _read_all_fd(fd)
        finally:
            os.close(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (observed.st_dev, observed.st_ino)
        ):
            return _LeafObservation("unsafe")
        return _LeafObservation(
            "blob",
            (opened.st_dev, opened.st_ino),
            hashlib.sha256(payload).hexdigest(),
            len(payload),
        )

    def create_temp(self) -> tuple[int, str, tuple[int, int]]:
        for _attempt in range(8):
            leaf = f".briefloop-bundle-{secrets.token_hex(16)}.tmp"
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                fd = os.open(leaf, flags, 0o600, dir_fd=self.parent_fd)
            except FileExistsError:
                continue
            except OSError as exc:
                raise ReportBundleProjectionError(
                    "bundle projection staging failed."
                ) from exc
            created = os.fstat(fd)
            if not stat.S_ISREG(created.st_mode):
                os.close(fd)
                raise ReportBundleProjectionError(
                    "bundle projection staging failed."
                )
            return fd, leaf, (created.st_dev, created.st_ino)
        raise ReportBundleProjectionError("bundle projection staging failed.")

    def replace(self, temporary: str, target: str) -> None:
        try:
            os.replace(
                temporary,
                target,
                src_dir_fd=self.parent_fd,
                dst_dir_fd=self.parent_fd,
            )
        except OSError as exc:
            raise ReportBundleProjectionError(
                "bundle projection publication failed."
            ) from exc

    def unlink_if_identity(self, leaf: str, identity: tuple[int, int]) -> None:
        try:
            observed = os.stat(
                leaf,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
            if (
                stat.S_ISREG(observed.st_mode)
                and (observed.st_dev, observed.st_ino) == identity
            ):
                os.unlink(leaf, dir_fd=self.parent_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def sync(self) -> None:
        try:
            os.fsync(self.parent_fd)
        except OSError:
            pass


@dataclass
class _StagedProjection:
    parent: _ProjectionParent
    target: str
    expected: _LeafObservation
    temporary: str
    identity: tuple[int, int]
    sha256: str
    size: int
    published: bool = False

    def publish(self) -> None:
        self.parent.reverify()
        if self.parent.observe(self.target) != self.expected:
            raise ReportBundleProjectionError("bundle projection target changed.")
        temporary = self.parent.observe(self.temporary)
        if (
            temporary.kind != "blob"
            or temporary.identity != self.identity
            or temporary.sha256 != self.sha256
            or temporary.size != self.size
        ):
            raise ReportBundleProjectionError("bundle projection staging changed.")
        self.parent.replace(self.temporary, self.target)
        final = self.parent.observe(self.target)
        if (
            final.kind != "blob"
            or final.identity != self.identity
            or final.sha256 != self.sha256
            or final.size != self.size
        ):
            raise ReportBundleProjectionError(
                "bundle projection final verification failed."
            )
        self.parent.sync()
        self.published = True

    def cleanup(self) -> None:
        if not self.published:
            self.parent.unlink_if_identity(self.temporary, self.identity)


def build_report_bundle_manifest(
    *,
    workspace: str | Path,
    template_registry: ReportTemplateRegistry | None = None,
) -> dict[str, Any]:
    _require_safe_bundle_read()
    ws = Path(workspace).expanduser().resolve()
    finalize_report = _load_finalize_report(ws)
    hygiene: dict[str, Any] = {"status": "clean", "excluded_artifacts": []}
    delivery_records = _delivery_records(ws, finalize_report, hygiene=hygiene)
    audit_records = _audit_records(ws, finalize_report, hygiene=hygiene)
    hygiene["excluded_artifacts"] = sorted(
        hygiene["excluded_artifacts"],
        key=lambda item: (item["surface"], item["path"], item["reason"]),
    )
    if hygiene["excluded_artifacts"]:
        hygiene["status"] = "excluded_packaging_junk"
    template = _template_projection(
        ws,
        template_registry=template_registry or ReportTemplateRegistry.from_package(),
    )
    citation_profile = _citation_profile_projection(finalize_report)
    return {
        "schema_version": REPORT_BUNDLE_MANIFEST_SCHEMA_VERSION,
        "workspace": ".",
        "source": "finalize_report_projection",
        "semantics": "delivery_and_audit_bundle_projection_only",
        "template": template,
        "citation_profile": citation_profile,
        "packaging_hygiene": hygiene,
        "supplemental_guidance": {
            "status": "available_when_archives_are_written",
            "semantics": "supplemental_guidance_non_authoritative_not_counted_as_artifacts",
            "artifact_count_policy": "excluded_from_delivery_bundle_and_audit_bundle_artifact_count",
            "delivery_archive_member": _DELIVERY_BUNDLE_README_MEMBER,
            "audit_archive_member": _AUDIT_BUNDLE_README_MEMBER,
        },
        "bundle_archives": {"status": "not_requested"},
        "delivery_bundle": {
            "status": "available",
            "semantics": "reader_facing_artifacts_only",
            "artifact_count": len(delivery_records),
            "artifacts": delivery_records,
        },
        "audit_bundle": {
            "status": "available",
            "semantics": "audit_control_artifacts_only_not_reader_delivery",
            "artifact_count": len(audit_records),
            "artifacts": audit_records,
        },
        "non_goals": [
            "delivery_approval",
            "gate_bypass",
            "publication_authorization",
            "semantic_support_assessment",
        ],
    }


def write_report_bundle_manifest(
    *,
    workspace: str | Path,
    output_path: str | Path | None = None,
    template_registry: ReportTemplateRegistry | None = None,
    write_archives: bool = False,
) -> dict[str, Any]:
    _require_safe_bundle_read()
    ws = Path(workspace).expanduser().resolve()
    target = _manifest_output_path(ws, output_path)
    _raise_if_reserved_archive_output(ws, target)
    parents: list[_ProjectionParent] = []
    staged: list[_StagedProjection] = []
    try:
        manifest_parent = _ProjectionParent.open(ws, target.parent)
        parents.append(manifest_parent)
        manifest_expected = _preflight_projection_target(
            manifest_parent,
            target.name,
        )
        output_dir = ws / "output"
        archive_parent: _ProjectionParent | None = None
        delivery_expected: _LeafObservation | None = None
        audit_expected: _LeafObservation | None = None
        if write_archives:
            if target.parent == output_dir:
                archive_parent = manifest_parent
            else:
                archive_parent = _ProjectionParent.open(ws, output_dir)
                parents.append(archive_parent)
            delivery_expected = _preflight_projection_target(
                archive_parent,
                "delivery_bundle.zip",
            )
            audit_expected = _preflight_projection_target(
                archive_parent,
                "audit_bundle.zip",
            )
        manifest = build_report_bundle_manifest(
            workspace=ws,
            template_registry=template_registry,
        )
        manifest["manifest_path"] = target.relative_to(ws).as_posix()
        if write_archives:
            if (
                archive_parent is None
                or delivery_expected is None
                or audit_expected is None
            ):
                raise ReportBundleProjectionError(
                    "bundle archive preflight is unavailable."
                )
            delivery_records = _records_from_bundle(manifest, "delivery_bundle")
            audit_records = _records_from_bundle(manifest, "audit_bundle")
            delivery = _stage_zip_projection(
                parent=archive_parent,
                target="delivery_bundle.zip",
                expected=delivery_expected,
                workspace=ws,
                records=delivery_records,
                surface="delivery",
            )
            staged.append(delivery)
            audit = _stage_zip_projection(
                parent=archive_parent,
                target="audit_bundle.zip",
                expected=audit_expected,
                workspace=ws,
                records=audit_records,
                surface="audit",
            )
            staged.append(audit)
            manifest["bundle_archives"] = {
                "status": "generated",
                "semantics": "clean_archives_from_report_bundle_manifest",
                "delivery": _staged_archive_record(
                    ws,
                    output_dir / "delivery_bundle.zip",
                    delivery,
                    artifact_count=len(delivery_records),
                ),
                "audit": _staged_archive_record(
                    ws,
                    output_dir / "audit_bundle.zip",
                    audit,
                    artifact_count=len(audit_records),
                ),
            }
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        staged.append(
            _stage_bytes_projection(
                parent=manifest_parent,
                target=target.name,
                expected=manifest_expected,
                payload=manifest_bytes,
            )
        )
        for parent in parents:
            parent.reverify()
        for projection in staged:
            if projection.parent.observe(projection.target) != projection.expected:
                raise ReportBundleProjectionError(
                    "bundle projection target changed."
                )
        for projection in staged:
            projection.publish()
        return manifest
    finally:
        for projection in staged:
            projection.cleanup()
        for parent in reversed(parents):
            parent.close()


def _manifest_output_path(workspace: Path, output_path: str | Path | None) -> Path:
    target = Path(output_path).expanduser() if output_path else workspace / "output" / "report_bundle_manifest.json"
    if not target.is_absolute():
        target = workspace / target
    target = Path(os.path.abspath(target))
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise ReportBundleProjectionError("bundle manifest output must stay inside the workspace.") from exc
    return target


def _raise_if_reserved_archive_output(workspace: Path, target: Path) -> None:
    reserved = {
        Path(os.path.abspath(workspace / "output" / "delivery_bundle.zip")),
        Path(os.path.abspath(workspace / "output" / "audit_bundle.zip")),
    }
    if target in reserved:
        rel = target.relative_to(workspace).as_posix()
        raise ReportBundleProjectionError(
            f"bundle manifest output path is reserved for clean bundle archives: {rel}"
        )


def _write_bundle_archives(
    workspace: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Safely publish both archives for the existing deterministic seam."""

    _require_safe_bundle_read()
    output_dir = workspace / "output"
    parent = _ProjectionParent.open(workspace, output_dir)
    staged: list[_StagedProjection] = []
    try:
        delivery_records = _records_from_bundle(manifest, "delivery_bundle")
        audit_records = _records_from_bundle(manifest, "audit_bundle")
        delivery_expected = _preflight_projection_target(
            parent,
            "delivery_bundle.zip",
        )
        audit_expected = _preflight_projection_target(
            parent,
            "audit_bundle.zip",
        )
        delivery = _stage_zip_projection(
            parent=parent,
            target="delivery_bundle.zip",
            expected=delivery_expected,
            workspace=workspace,
            records=delivery_records,
            surface="delivery",
        )
        staged.append(delivery)
        audit = _stage_zip_projection(
            parent=parent,
            target="audit_bundle.zip",
            expected=audit_expected,
            workspace=workspace,
            records=audit_records,
            surface="audit",
        )
        staged.append(audit)
        parent.reverify()
        for projection in staged:
            if parent.observe(projection.target) != projection.expected:
                raise ReportBundleProjectionError(
                    "bundle projection target changed."
                )
        for projection in staged:
            projection.publish()
        return {
            "status": "generated",
            "semantics": "clean_archives_from_report_bundle_manifest",
            "delivery": _staged_archive_record(
                workspace,
                output_dir / "delivery_bundle.zip",
                delivery,
                artifact_count=len(delivery_records),
            ),
            "audit": _staged_archive_record(
                workspace,
                output_dir / "audit_bundle.zip",
                audit,
                artifact_count=len(audit_records),
            ),
        }
    finally:
        for projection in staged:
            projection.cleanup()
        parent.close()


def _records_from_bundle(manifest: dict[str, Any], key: str) -> list[dict[str, Any]]:
    bundle = manifest.get(key)
    artifacts = bundle.get("artifacts") if isinstance(bundle, dict) else None
    if not isinstance(artifacts, list):
        return []
    return [item for item in artifacts if isinstance(item, dict)]


def _write_zip_to_fd(
    *,
    fd: int,
    workspace: Path,
    records: list[dict[str, Any]],
    surface: str,
) -> None:
    with os.fdopen(os.dup(fd), "w+b") as stream:
        with zipfile.ZipFile(
            stream,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zf:
            _write_bundle_readme(zf, surface=surface)
            for record in sorted(
                records,
                key=lambda item: str(item.get("path") or ""),
            ):
                rel = str(record.get("path") or "").strip()
                if not rel:
                    continue
                decision = classify_workspace_member(
                    workspace,
                    rel,
                    surface="bundle",
                )
                if decision.status != "include" or decision.relative_path is None:
                    raise ReportBundleProjectionError(
                        f"bundle member is not hygienic: {rel}: "
                        f"{decision.reason_code}"
                    )
                verified_rel, payload = _read_verified_workspace_member(
                    workspace,
                    rel,
                    surface="bundle",
                )
                if verified_rel != rel:
                    raise ReportBundleProjectionError(
                        f"bundle member path changed during verification: {rel}"
                    )
                expected_sha = str(record.get("sha256") or "")
                expected_size = record.get("size_bytes")
                if (
                    hashlib.sha256(payload).hexdigest() != expected_sha
                    or len(payload) != expected_size
                ):
                    raise ReportBundleProjectionError(
                        f"bundle member changed after manifest projection: {rel}"
                    )
                arcname = _archive_member_name(rel, surface=surface)
                info = zipfile.ZipInfo(arcname)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, payload)
        stream.flush()


def _supports_safe_bundle_read() -> bool:
    """Return whether retained, no-follow member reads are available."""

    required_dir_fd = (os.open, os.stat)
    return (
        all(function in os.supports_dir_fd for function in required_dir_fd)
        and os.stat in os.supports_follow_symlinks
        and bool(getattr(os, "O_DIRECTORY", 0))
        and bool(getattr(os, "O_NOFOLLOW", 0))
        and callable(getattr(os, "fstat", None))
        and callable(getattr(os, "read", None))
    )


def _require_safe_bundle_read() -> None:
    if not _supports_safe_bundle_read():
        raise ReportBundleProjectionError("bundle_projection_read_unsupported")


def _validate_projection_leaf(leaf: str) -> None:
    if (
        not leaf
        or leaf in {".", ".."}
        or "/" in leaf
        or "\\" in leaf
        or Path(leaf).name != leaf
    ):
        raise ReportBundleProjectionError("bundle projection target is invalid.")


def _read_all_fd(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_all_fd(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("bundle projection short write")
        view = view[written:]


def _preflight_projection_target(
    parent: _ProjectionParent,
    target: str,
) -> _LeafObservation:
    parent.reverify()
    observed = parent.observe(target)
    if observed.kind not in {"absent", "blob"}:
        raise ReportBundleProjectionError(
            "bundle projection target is not a replaceable regular file."
        )
    return observed


def _stage_projection(
    *,
    parent: _ProjectionParent,
    target: str,
    expected: _LeafObservation,
    writer: Callable[[int], None],
) -> _StagedProjection:
    fd, temporary, identity = parent.create_temp()
    try:
        writer(fd)
        os.fsync(fd)
        current = os.fstat(fd)
        payload = _read_all_fd(fd)
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
            or current.st_size != len(payload)
        ):
            raise OSError("bundle projection temporary verification failed")
    except Exception as exc:
        os.close(fd)
        parent.unlink_if_identity(temporary, identity)
        if isinstance(exc, ReportBundleProjectionError):
            raise
        raise ReportBundleProjectionError(
            "bundle projection staging failed."
        ) from exc
    os.close(fd)
    return _StagedProjection(
        parent=parent,
        target=target,
        expected=expected,
        temporary=temporary,
        identity=identity,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )


def _stage_bytes_projection(
    *,
    parent: _ProjectionParent,
    target: str,
    expected: _LeafObservation,
    payload: bytes,
) -> _StagedProjection:
    return _stage_projection(
        parent=parent,
        target=target,
        expected=expected,
        writer=lambda fd: _write_all_fd(fd, payload),
    )


def _stage_zip_projection(
    *,
    parent: _ProjectionParent,
    target: str,
    expected: _LeafObservation,
    workspace: Path,
    records: list[dict[str, Any]],
    surface: str,
) -> _StagedProjection:
    return _stage_projection(
        parent=parent,
        target=target,
        expected=expected,
        writer=lambda fd: _write_zip_to_fd(
            fd=fd,
            workspace=workspace,
            records=records,
            surface=surface,
        ),
    )


def _staged_archive_record(
    workspace: Path,
    path: Path,
    staged: _StagedProjection,
    *,
    artifact_count: int,
) -> dict[str, Any]:
    return {
        "path": _workspace_relative(workspace, path),
        "sha256": staged.sha256,
        "size_bytes": staged.size,
        "artifact_count": artifact_count,
    }


def _write_bundle_readme(zf: zipfile.ZipFile, *, surface: str) -> None:
    if surface == "delivery":
        arcname = _DELIVERY_BUNDLE_README_MEMBER
        text = _DELIVERY_BUNDLE_README
    elif surface == "audit":
        arcname = _AUDIT_BUNDLE_README_MEMBER
        text = _AUDIT_BUNDLE_README
    else:
        return
    info = zipfile.ZipInfo(arcname)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, text)


def _archive_member_name(rel_path: str, *, surface: str) -> str:
    rel = Path(rel_path).as_posix()
    if surface == "delivery" and rel.startswith("output/delivery/"):
        rel = rel.removeprefix("output/delivery/")
    return f"{surface}/{rel}".replace("//", "/")


def _archive_record(workspace: Path, path: Path, *, artifact_count: int) -> dict[str, Any]:
    return {
        "path": _workspace_relative(workspace, path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "artifact_count": artifact_count,
    }


def _load_finalize_report(workspace: Path) -> dict[str, Any]:
    path = workspace / "output" / "intermediate" / "finalize_report.json"
    if not path.exists():
        raise ReportBundleProjectionError(
            "finalize_report.json is required before building report bundles."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportBundleProjectionError(f"finalize_report.json is unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReportBundleProjectionError(f"finalize_report.json is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportBundleProjectionError("finalize_report.json must contain an object.")
    if payload.get("status") != "pass":
        raise ReportBundleProjectionError("finalize_report.json status must be pass.")
    reader_clean = payload.get("reader_clean")
    if not isinstance(reader_clean, dict) or reader_clean.get("status") != "pass":
        raise ReportBundleProjectionError("finalize_report.json reader_clean.status must be pass.")
    audit_binding_reasons = require_finalize_audit_binding_pass(
        interpret_finalize_audit_binding(
            workspace=workspace,
            finalize_report=payload,
        )
    )
    if audit_binding_reasons:
        raise ReportBundleProjectionError(
            "finalize_report.json audit_binding must pass before building report bundles: "
            + "; ".join(audit_binding_reasons)
        )
    return payload


def _delivery_records(
    workspace: Path,
    finalize_report: dict[str, Any],
    *,
    hygiene: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_artifacts = finalize_report.get("delivery_artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ReportBundleProjectionError("finalize_report.json delivery_artifacts must be non-empty.")
    raw_hashes = finalize_report.get("delivery_artifact_sha256")
    if not isinstance(raw_hashes, dict) or not raw_hashes:
        raise ReportBundleProjectionError(
            "finalize_report.json delivery_artifact_sha256 must be a non-empty object."
        )
    hashes = raw_hashes
    records: list[dict[str, Any]] = []
    delivery_root = (workspace / "output" / "delivery").resolve()
    for raw in raw_artifacts:
        if not isinstance(raw, str) or not raw.strip():
            raise ReportBundleProjectionError("finalize_report.json contains an invalid delivery artifact path.")
        decision = classify_workspace_member(
            workspace,
            raw,
            surface="delivery",
        )
        if decision.status != "include" or decision.relative_path is None:
            _record_hygiene_exclusion(
                decision.relative_path or "outside_workspace",
                hygiene=hygiene,
                surface="delivery",
                reason=decision.reason_code or "workspace_member_excluded",
            )
            continue
        path = _resolve_workspace_path(workspace, decision.relative_path)
        try:
            path.relative_to(delivery_root)
        except ValueError as exc:
            raise ReportBundleProjectionError(
                "delivery artifacts must be under output/delivery/."
            ) from exc
        expected_sha = _hash_for_path(hashes, raw=raw, workspace=workspace, path=path)
        if not expected_sha:
            raise ReportBundleProjectionError(
                f"delivery artifact hash missing: {_workspace_relative(workspace, path)}"
            )
        actual_sha = _sha256_file(path)
        if expected_sha != actual_sha:
            raise ReportBundleProjectionError(
                f"delivery artifact hash mismatch: {_workspace_relative(workspace, path)}"
            )
        _validate_reader_delivery_artifact(workspace, path)
        records.append(_artifact_record(workspace, path, role="reader_delivery"))
    if not records:
        raise ReportBundleProjectionError(
            "finalize_report.json delivery_artifacts did not include packageable reader artifacts."
        )
    return records


def _validate_reader_delivery_artifact(workspace: Path, path: Path) -> None:
    rel = _workspace_relative(workspace, path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        result = detect_reader_residue_in_docx(path, artifact=rel)
    else:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReportBundleProjectionError(f"reader delivery artifact is unreadable: {rel}: {exc}") from exc
        result = detect_reader_residue(text, artifact=rel)
    if result.status != "pass":
        finding_kinds = sorted({finding.kind for finding in result.findings})
        detail = ", ".join(finding_kinds) or "reader_residue"
        raise ReportBundleProjectionError(
            f"reader delivery artifact failed reader-clean residue scan: {rel}: {detail}"
        )


def _audit_records(
    workspace: Path,
    finalize_report: dict[str, Any],
    *,
    hygiene: dict[str, Any],
) -> list[dict[str, Any]]:
    _validate_present_quality_artifacts(workspace)
    candidates = [
        ("finalize_report", workspace / "output" / "intermediate" / "finalize_report.json"),
        ("claim_ledger", workspace / "output" / "intermediate" / "claim_ledger.json"),
        ("audited_brief", workspace / "output" / "intermediate" / "audited_brief.md"),
        ("audit_report", workspace / "output" / "intermediate" / "audit_report.json"),
        ("artifact_registry", workspace / "output" / "intermediate" / "artifact_registry.json"),
        ("runtime_manifest", workspace / "output" / "intermediate" / "runtime_manifest.json"),
        ("workflow_state", workspace / "output" / "intermediate" / "workflow_state.json"),
        ("event_log", workspace / "output" / "intermediate" / "event_log.jsonl"),
        ("auditor_gate_report", workspace / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json"),
        (
            "finalize_gate_report",
            workspace / "output" / "intermediate" / "gates" / "finalize_quality_gate_report.json",
        ),
        ("source_appendix", workspace / "output" / "source_appendix.md"),
        ("source_appendix_trace", _optional_report_path(workspace, finalize_report, "source_appendix_trace")),
        ("atomic_claim_graph", workspace / "output" / "intermediate" / "atomic_claim_graph.json"),
        ("evidence_span_registry", workspace / "output" / "intermediate" / "evidence_span_registry.json"),
        ("claim_support_matrix", workspace / "output" / "intermediate" / "claim_support_matrix.json"),
        ("semantic_assessment_report", workspace / "output" / "intermediate" / "semantic_assessment_report.json"),
        ("quality_panel", workspace / "output" / "intermediate" / "quality_panel.json"),
        ("quality_summary", workspace / "output" / "intermediate" / "quality_summary.md"),
        ("quality_panel_html", workspace / "output" / "intermediate" / "quality_panel.html"),
    ]
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role, path in candidates:
        if path is None:
            continue
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            decision = classify_workspace_member(workspace, path, surface="audit")
            _record_hygiene_exclusion(
                decision.relative_path or "outside_workspace",
                hygiene=hygiene,
                surface="audit",
                reason=decision.reason_code or "workspace_member_unreadable",
            )
            continue
        decision = classify_workspace_member(
            workspace,
            path,
            surface="audit",
        )
        if decision.status != "include":
            _record_hygiene_exclusion(
                decision.relative_path or "outside_workspace",
                hygiene=hygiene,
                surface="audit",
                reason=decision.reason_code or "workspace_member_excluded",
            )
            continue
        if decision.relative_path is None:
            continue
        rel = decision.relative_path
        if rel.startswith("output/delivery/") or rel in seen:
            continue
        try:
            verified_rel, payload = _read_verified_workspace_member(
                workspace,
                path,
                surface="audit",
            )
        except ReportBundleProjectionError:
            _record_hygiene_exclusion(
                rel,
                hygiene=hygiene,
                surface="audit",
                reason="workspace_member_identity_changed",
            )
            continue
        if verified_rel != rel:
            raise ReportBundleProjectionError(
                f"audit member path changed during verification: {rel}"
            )
        seen.add(rel)
        records.append(_artifact_record_from_bytes(rel, payload, role=role))
    return records


def _validate_present_quality_artifacts(workspace: Path) -> None:
    quality_paths = {
        "quality_panel": workspace / "output" / "intermediate" / "quality_panel.json",
        "quality_summary": workspace / "output" / "intermediate" / "quality_summary.md",
        "quality_panel_html": workspace / "output" / "intermediate" / "quality_panel.html",
    }
    if not any(path.exists() for path in quality_paths.values()):
        return

    panel_path = quality_paths["quality_panel"]
    panel_payload = _load_valid_quality_panel_payload(workspace, panel_path)
    if quality_paths["quality_summary"].exists():
        _validate_quality_summary_binding(workspace, quality_paths["quality_summary"], panel_path, panel_payload)
    if quality_paths["quality_panel_html"].exists():
        _validate_quality_panel_html_binding(workspace, quality_paths["quality_panel_html"], panel_path, panel_payload)


def _load_valid_quality_panel_payload(workspace: Path, panel_path: Path) -> dict[str, Any]:
    if not panel_path.exists():
        _raise_quality_artifact_error(
            workspace,
            panel_path,
            "quality_panel_missing",
        )
    try:
        payload = json.loads(panel_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        _raise_quality_artifact_error(workspace, panel_path, "quality_panel_unreadable")
    except json.JSONDecodeError:
        _raise_quality_artifact_error(workspace, panel_path, "quality_panel_parse_error")
    if not isinstance(payload, dict):
        _raise_quality_artifact_error(workspace, panel_path, "quality_panel_invalid:not_object")
    reason = validate_quality_panel_payload(payload)
    if reason:
        _raise_quality_artifact_error(workspace, panel_path, f"quality_panel_invalid:{reason}")
    return payload


def _validate_quality_summary_binding(
    workspace: Path,
    summary_path: Path,
    panel_path: Path,
    panel_payload: dict[str, Any],
) -> None:
    try:
        text = summary_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _raise_quality_artifact_error(workspace, summary_path, "quality_summary_unreadable")
    reason = validate_quality_summary_markdown(text)
    if reason:
        _raise_quality_artifact_error(workspace, summary_path, reason)
    try:
        expected = render_quality_summary(panel_payload, quality_panel_sha256=_sha256_file(panel_path))
    except QualityPanelError as exc:
        _raise_quality_artifact_error(workspace, summary_path, f"quality_summary_render:{exc}")
    if text != expected:
        _raise_quality_artifact_error(workspace, summary_path, "quality_summary_stale_or_hand_edited")


def _validate_quality_panel_html_binding(
    workspace: Path,
    html_path: Path,
    panel_path: Path,
    panel_payload: dict[str, Any],
) -> None:
    try:
        text = html_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _raise_quality_artifact_error(workspace, html_path, "quality_panel_html_unreadable")
    reason = validate_quality_panel_html(text)
    if reason:
        _raise_quality_artifact_error(workspace, html_path, reason)
    try:
        expected = render_quality_panel_html(panel_payload, quality_panel_sha256=_sha256_file(panel_path))
    except QualityPanelError as exc:
        _raise_quality_artifact_error(workspace, html_path, f"quality_panel_html_render:{exc}")
    if text != expected:
        _raise_quality_artifact_error(workspace, html_path, "quality_panel_html_stale_or_hand_edited")


def _raise_quality_artifact_error(workspace: Path, path: Path, reason: str) -> None:
    try:
        rel = _workspace_relative(workspace, path)
    except ValueError:
        rel = path.as_posix()
    raise ReportBundleProjectionError(
        f"quality projection artifact invalid: {rel}: {reason}; rerun briefloop quality summarize"
    )


def _template_projection(
    workspace: Path,
    *,
    template_registry: ReportTemplateRegistry,
) -> dict[str, Any]:
    spec_path = workspace / "report_spec.yaml"
    if not spec_path.exists():
        return {"status": "not_available", "reason": "report_spec_missing"}
    try:
        spec = load_report_spec(spec_path)
    except (OSError, ReportSpecLoadError) as exc:
        return {"status": "invalid_report_spec", "reason": str(exc)}
    report_type = str(spec.get("report_type") or "").strip()
    template = template_registry.get_by_report_type(report_type)
    if template is None:
        return {"status": "not_available", "report_type": report_type, "reason": "template_missing"}
    return {
        "status": "available",
        "template_id": template.template_id,
        "report_type": template.report_type,
        "section_order": list(template.section_order),
        "semantics": "stable_section_order_only_not_renderer",
    }


def _citation_profile_projection(finalize_report: dict[str, Any]) -> dict[str, Any]:
    if "citation_profile" not in finalize_report:
        report = citation_profile_report(
            profile=DEFAULT_CITATION_PROFILE,
            source="legacy_finalize_report_default",
        )
        report["status"] = "legacy_default"
        report["semantics"] = "reader_delivery_citation_projection_and_audit_trace_split"
        return report

    raw_profile = finalize_report.get("citation_profile")
    profile = normalize_citation_profile(raw_profile)
    if not profile:
        raise ReportBundleProjectionError("finalize_report citation profile invalid: citation_profile")
    report = citation_profile_report(
        profile=profile,
        source=str(finalize_report.get("citation_profile_source") or "finalize_report"),
        warnings=[
            str(item)
            for item in finalize_report.get("citation_profile_warnings", [])
            if isinstance(item, str)
        ],
    )
    for source_field, target_field in (
        ("citation_profile_runtime_effect", "runtime_effect"),
        ("citation_profile_reader_citation_style", "reader_citation_style"),
        ("citation_profile_reader_metadata_level", "reader_metadata_level"),
        ("citation_profile_audit_trace_level", "audit_trace_level"),
    ):
        value = finalize_report.get(source_field)
        if value is not None and str(value).strip() != str(report.get(target_field) or ""):
            raise ReportBundleProjectionError(
                f"finalize_report citation profile invalid: {source_field}"
            )
    for source_field, target_field in (
        ("citation_profile_delivery_exposes_internal_ids", "delivery_exposes_internal_ids"),
        ("citation_profile_delivery_exposes_local_paths", "delivery_exposes_local_paths"),
        ("citation_profile_audit_bundle_keeps_trace", "audit_bundle_keeps_trace"),
    ):
        if source_field in finalize_report and finalize_report[source_field] is not report[target_field]:
            raise ReportBundleProjectionError(
                f"finalize_report citation profile invalid: {source_field}"
            )
    reason = validate_citation_profile_report(report)
    if reason:
        raise ReportBundleProjectionError(f"finalize_report citation profile invalid: {reason}")
    report["status"] = "available"
    report["semantics"] = "reader_delivery_citation_projection_and_audit_trace_split"
    return report


def _optional_report_path(workspace: Path, report: dict[str, Any], field: str) -> Path | None:
    raw = report.get(field)
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _resolve_workspace_path(workspace, raw)


def _resolve_workspace_path(workspace: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ReportBundleProjectionError(f"artifact path escapes workspace: {raw}") from exc
    if not resolved.exists() or not resolved.is_file():
        raise ReportBundleProjectionError(f"artifact path is missing: {raw}")
    return resolved


def _hash_for_path(
    hashes: dict[str, Any],
    *,
    raw: str,
    workspace: Path,
    path: Path,
) -> str:
    rel = _workspace_relative(workspace, path)
    for key in (raw, rel, path.as_posix(), str(path)):
        value = hashes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _artifact_record(workspace: Path, path: Path, *, role: str) -> dict[str, Any]:
    rel, payload = _read_verified_workspace_member(
        workspace,
        path,
        surface="bundle",
    )
    return _artifact_record_from_bytes(rel, payload, role=role)


def _artifact_record_from_bytes(
    relative_path: str,
    payload: bytes,
    *,
    role: str,
) -> dict[str, Any]:
    record = {
        "path": relative_path,
        "role": role,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    name = Path(relative_path).name
    fallback = _ascii_fallback_name(name)
    if fallback != name:
        record["ascii_fallback_name"] = fallback
    return record


def _read_verified_workspace_member(
    workspace: Path,
    candidate: str | Path,
    *,
    surface: str,
) -> tuple[str, bytes]:
    """Read one classified member through retained no-follow directory handles."""

    _require_safe_bundle_read()
    decision = classify_workspace_member(workspace, candidate, surface=surface)
    if decision.status != "include" or decision.relative_path is None:
        raise ReportBundleProjectionError(
            "workspace member is not hygienic: "
            f"{decision.relative_path or 'outside_workspace'}: "
            f"{decision.reason_code or 'workspace_member_excluded'}"
        )
    root = workspace.expanduser().resolve(strict=True)
    parts = Path(decision.relative_path).parts
    directory_fd = -1
    chunks: list[bytes] = []
    before: os.stat_result | None = None
    try:
        directory_fd = os.open(
            root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        for part in parts[:-1]:
            observed = os.stat(
                part,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(observed.st_mode):
                raise OSError("workspace member parent is not a directory")
            next_fd = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            opened = os.fstat(next_fd)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino)
                != (observed.st_dev, observed.st_ino)
            ):
                os.close(next_fd)
                raise OSError("workspace member parent changed")
            os.close(directory_fd)
            directory_fd = next_fd
        observed = os.stat(
            parts[-1],
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(observed.st_mode):
            raise OSError("workspace member is not regular")
        leaf_fd = os.open(
            parts[-1],
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
        try:
            before = os.fstat(leaf_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or (before.st_dev, before.st_ino)
                != (observed.st_dev, observed.st_ino)
            ):
                raise OSError("workspace member is not regular")
            while True:
                chunk = os.read(leaf_fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(leaf_fd)
            if (
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or before.st_size != after.st_size
            ):
                raise OSError("workspace member changed during read")
        finally:
            os.close(leaf_fd)
    except (OSError, NotImplementedError) as exc:
        raise ReportBundleProjectionError(
            f"workspace member is unreadable or changed: {decision.relative_path}"
        ) from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
    payload = b"".join(chunks)
    if before is None or len(payload) != before.st_size:
        raise ReportBundleProjectionError(
            f"workspace member changed during read: {decision.relative_path}"
        )
    return decision.relative_path, payload


def _record_hygiene_exclusion(
    relative_path: str,
    *,
    hygiene: dict[str, Any],
    surface: str,
    reason: str,
) -> None:
    exclusions = hygiene.setdefault("excluded_artifacts", [])
    exclusions.append({
        "path": relative_path,
        "surface": surface,
        "reason": reason,
    })


def _ascii_fallback_name(filename: str) -> str:
    path = Path(filename)
    suffix = path.suffix
    raw_stem = path.stem or filename
    encoded_stem = raw_stem.encode("ascii", "ignore").decode("ascii")
    fallback_stem = _ASCII_SAFE_RE.sub("-", encoded_stem).strip(".-")
    safe_suffix = suffix if suffix and suffix.encode("ascii", "ignore").decode("ascii") == suffix else ""
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:12]
    if fallback_stem:
        return f"{fallback_stem}-{digest}{safe_suffix}"
    return f"artifact-{digest}{safe_suffix}"


def _workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace).as_posix()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
