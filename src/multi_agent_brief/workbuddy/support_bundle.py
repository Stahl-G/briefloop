"""Secret-safe WorkBuddy support bundle packaging."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
import zipfile


SUPPORT_BUNDLE_SCHEMA_VERSION = "briefloop.workbuddy_support_bundle.v1"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
EMBEDDED_MANIFEST = "support_bundle_manifest.json"
_TEXT_EXTENSIONS = {
    ".csv",
    ".html",
    ".json",
    ".jsonl",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}
_ROOT_FILES = {
    "config.yaml",
    "sources.yaml",
    "user.md",
    "report_spec.yaml",
    "onboarding.json",
    "profile.yaml",
}
_FORBIDDEN_PARTS = {
    ".git",
    "__pycache__",
    "private_planning",
    "docs/internal",
    ".venv",
    "venv",
    "dist",
    "build",
}
_FORBIDDEN_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".envrc",
}
_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|credential|authorization|bearer)"
)
_ASSIGNMENT_RE = re.compile(r"^(\s*[^#\n:=]{0,120}?(?:=|:)\s*)(.+?)(\s*)$")


class WorkBuddySupportBundleError(RuntimeError):
    """Raised when a WorkBuddy support bundle cannot be produced safely."""


@dataclass(frozen=True)
class WorkBuddySupportBundleResult:
    zip_path: Path
    manifest_path: Path
    zip_sha256: str
    included_files: tuple[str, ...]
    excluded_files: tuple[dict[str, str], ...]
    redacted_files: tuple[str, ...]
    manifest: dict[str, Any]


def package_workbuddy_support_bundle(
    *,
    workspace: str | Path,
    output_dir: str | Path,
) -> WorkBuddySupportBundleResult:
    """Create a secret-redacted support bundle from selected workspace files."""

    ws = Path(workspace).expanduser().resolve()
    if not ws.exists() or not ws.is_dir():
        raise WorkBuddySupportBundleError(f"workspace does not exist: {ws}")
    output = Path(output_dir).expanduser().resolve()
    _validate_output_dir(output=output, workspace=ws)
    output.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug(ws.name or "workspace")
    zip_path = output / f"{slug}-workbuddy-support.zip"
    manifest_path = output / f"{slug}-workbuddy-support.manifest.json"

    entries, excluded = _support_entries(ws)
    if not entries:
        raise WorkBuddySupportBundleError("no support-safe files found to package")

    redacted_files: list[str] = []
    manifest_files: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            rel = entry.relative_to(ws).as_posix()
            raw = entry.read_bytes()
            data, redacted = _redact_bytes(raw)
            if redacted:
                redacted_files.append(rel)
            archive_path = f"workspace/{rel}"
            _write_zip_entry(archive, archive_path, data)
            manifest_files.append(
                {
                    "path": archive_path,
                    "source_path": rel,
                    "sha256": sha256(data).hexdigest(),
                    "size": len(data),
                    "redacted": redacted,
                }
            )
        embedded_manifest = _manifest_payload(
            workspace=ws,
            zip_path=zip_path,
            files=manifest_files,
            excluded=excluded,
            redacted_files=redacted_files,
            zip_sha256="",
            include_zip_sha=False,
        )
        embedded_bytes = _json_bytes(embedded_manifest)
        _write_zip_entry(archive, EMBEDDED_MANIFEST, embedded_bytes)

    zip_digest = _sha256_file(zip_path)
    sidecar_manifest = _manifest_payload(
        workspace=ws,
        zip_path=zip_path,
        files=manifest_files,
        excluded=excluded,
        redacted_files=redacted_files,
        zip_sha256=zip_digest,
        include_zip_sha=True,
    )
    sidecar_manifest["included_files"].append(
        {
            "path": EMBEDDED_MANIFEST,
            "source_path": EMBEDDED_MANIFEST,
            "sha256": sha256(embedded_bytes).hexdigest(),
            "size": len(embedded_bytes),
            "redacted": False,
        }
    )
    manifest_path.write_bytes(_json_bytes(sidecar_manifest))

    return WorkBuddySupportBundleResult(
        zip_path=zip_path,
        manifest_path=manifest_path,
        zip_sha256=zip_digest,
        included_files=tuple(item["path"] for item in sidecar_manifest["included_files"]),
        excluded_files=tuple(excluded),
        redacted_files=tuple(sorted(set(redacted_files))),
        manifest=sidecar_manifest,
    )


def validate_workbuddy_support_bundle(
    *,
    zip_path: str | Path,
    manifest_path: str | Path,
) -> list[str]:
    """Return validation errors for a generated support bundle."""

    zip_file = Path(zip_path)
    manifest_file = Path(manifest_path)
    errors: list[str] = []
    if not zip_file.exists():
        return [f"missing zip: {zip_file}"]
    if not manifest_file.exists():
        return [f"missing manifest: {manifest_file}"]
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"manifest unreadable: {exc}"]
    if manifest.get("schema_version") != SUPPORT_BUNDLE_SCHEMA_VERSION:
        errors.append("manifest schema_version mismatch")
    if manifest.get("runtime_effect") != "packaging_only_read_only":
        errors.append("manifest runtime_effect must be packaging_only_read_only")
    if manifest.get("share_workspace_zip_allowed") is not False:
        errors.append("manifest must forbid sharing whole workspace zips")
    if manifest.get("zip_sha256") != _sha256_file(zip_file):
        errors.append("zip sha256 mismatch")
    included_files = manifest.get("included_files")
    if not isinstance(included_files, list):
        errors.append("manifest included_files must be a list")
        included_files = []
    try:
        with zipfile.ZipFile(zip_file) as archive:
            names = sorted(archive.namelist())
            expected_names = sorted(
                item.get("path", "") if isinstance(item, dict) else "" for item in included_files
            )
            if names != expected_names:
                errors.append("zip file list does not match manifest")
            for item in included_files:
                if not isinstance(item, dict):
                    errors.append("manifest included_files contains non-object item")
                    continue
                rel = item.get("path")
                if not isinstance(rel, str) or not rel:
                    errors.append("manifest contains invalid path")
                    continue
                _validate_archive_path(rel, errors)
                try:
                    data = archive.read(rel)
                except KeyError:
                    errors.append(f"zip missing manifest file: {rel}")
                    continue
                if sha256(data).hexdigest() != item.get("sha256"):
                    errors.append(f"sha256 mismatch for {rel}")
                if _contains_secret_value(data):
                    errors.append(f"possible unredacted secret in {rel}")
    except zipfile.BadZipFile as exc:
        errors.append(f"bad zip file: {exc}")
    return errors


def _support_entries(workspace: Path) -> tuple[list[Path], list[dict[str, str]]]:
    entries: list[Path] = []
    excluded: list[dict[str, str]] = []
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink():
            excluded.append(_excluded_record(workspace, path, "symlink"))
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        reason = _exclusion_reason(rel, path)
        if reason:
            excluded.append({"path": rel, "reason": reason})
            continue
        if _is_support_file(rel, path):
            entries.append(path)
    return entries, excluded


def _is_support_file(rel: str, path: Path) -> bool:
    if rel in _ROOT_FILES:
        return True
    if rel.startswith("output/") and path.suffix.lower() in _TEXT_EXTENSIONS:
        return True
    if rel.startswith("input/") and path.suffix.lower() in _TEXT_EXTENSIONS:
        return True
    return False


def _exclusion_reason(rel: str, path: Path) -> str | None:
    parts = set(Path(rel).parts)
    for forbidden in _FORBIDDEN_PARTS:
        if forbidden in rel or forbidden in parts:
            return "forbidden_private_or_generated_path"
    name = path.name
    if name in _FORBIDDEN_FILENAMES or name.startswith(".env."):
        return "secret_env_file"
    if path.suffix.lower() in {".zip", ".gz", ".tar", ".tgz"}:
        return "archive_file"
    if path.suffix.lower() not in _TEXT_EXTENSIONS and rel not in _ROOT_FILES:
        return "non_text_or_not_support_surface"
    return None


def _redact_bytes(data: bytes) -> tuple[bytes, bool]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data, False
    redacted = False
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        line_end = line[len(line_body) :]
        if _SECRET_KEY_RE.search(line_body):
            match = _ASSIGNMENT_RE.match(line_body)
            if match:
                line = f"{match.group(1)}<redacted>{match.group(3)}{line_end}"
                redacted = True
            elif line_body.strip():
                line = "<redacted secret-bearing line>" + line_end
                redacted = True
        lines.append(line)
    if not redacted:
        return data, False
    return "".join(lines).encode("utf-8"), True


def _contains_secret_value(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    for line in text.splitlines():
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        value = match.group(2).strip().strip('",')
        if not _SECRET_KEY_RE.search(key):
            continue
        if value in {"", "false", "true", "null", "{", "[", "<redacted>"}:
            continue
        if value.startswith("<redacted>"):
            continue
        if _SECRET_KEY_RE.search(line):
            return True
    return False


def _manifest_payload(
    *,
    workspace: Path,
    zip_path: Path,
    files: list[dict[str, Any]],
    excluded: list[dict[str, str]],
    redacted_files: list[str],
    zip_sha256: str,
    include_zip_sha: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
        "runtime_effect": "packaging_only_read_only",
        "workspace_name": workspace.name,
        "zip_path": str(zip_path),
        "semantics": "workbuddy_support_bundle_not_delivery_bundle",
        "share_workspace_zip_allowed": False,
        "included_files": list(files),
        "excluded_files": list(excluded),
        "redacted_files": sorted(set(redacted_files)),
        "secret_policy": {
            "env_files_included": False,
            "secret_values_reported": False,
            "text_secret_lines_redacted": True,
            "rotate_keys_if_workspace_zip_was_shared": True,
        },
        "non_goals": [
            "delivery_approval",
            "gate_bypass",
            "release_authority",
            "semantic_truth_proof",
            "reader_delivery_package",
        ],
        "boundary": "secret_safe_support_bundle_not_delivery_gate_release_authority",
    }
    if include_zip_sha:
        payload["zip_sha256"] = zip_sha256
    return payload


def _validate_output_dir(*, output: Path, workspace: Path) -> None:
    try:
        output.relative_to(workspace)
    except ValueError:
        return
    raise WorkBuddySupportBundleError(
        "support bundle output directory must not be inside the workspace; "
        "choose a separate folder to avoid self-including generated packages."
    )


def _write_zip_entry(archive: zipfile.ZipFile, archive_path: str, data: bytes) -> None:
    _validate_archive_path(archive_path, [])
    info = zipfile.ZipInfo(archive_path)
    info.date_time = FIXED_ZIP_TIME
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _validate_archive_path(path: str, errors: list[str]) -> None:
    if path.startswith("/") or path.startswith("../") or "/../" in path or path == "..":
        errors.append(f"unsafe archive path: {path}")
    if path.endswith("/"):
        errors.append(f"directory archive path not allowed: {path}")
    parts = Path(path).parts
    if ".env" in parts or any(part.startswith(".env.") for part in parts):
        errors.append(f"env file must not be included: {path}")
    if "private_planning" in parts:
        errors.append(f"private planning path must not be included: {path}")


def _excluded_record(workspace: Path, path: Path, reason: str) -> dict[str, str]:
    return {"path": path.relative_to(workspace).as_posix(), "reason": reason}


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return slug or "workspace"
