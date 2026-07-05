"""Build the source-clone WorkBuddy Skill package."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import zipfile

from multi_agent_brief import __version__


SKILL_SOURCE_RELATIVE = Path(".agents/skills/briefloop-workbuddy")
PACKAGE_PREFIX = "briefloop"
EMBEDDED_MANIFEST = "briefloop-workbuddy-skill-manifest.json"
MANIFEST_SCHEMA_VERSION = "briefloop.workbuddy_skill_pack_manifest.v1"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
DETERMINISTIC_GENERATED_AT = "1970-01-01T00:00:00+00:00"
REQUIRED_SKILL_FILES = (
    "SKILL.md",
    "references/artifact-boundary.md",
    "references/quickstart.md",
    "references/repair-protocol.md",
    "references/status-and-gates.md",
    "references/workbuddy-safety.md",
    "references/workspace-workflow.md",
)
FORBIDDEN_PATH_PARTS = {
    ".git",
    "__pycache__",
    ".DS_Store",
    "private_planning",
    "output",
}


class WorkBuddySkillPackError(RuntimeError):
    """Raised when the WorkBuddy Skill package cannot be built or verified."""


@dataclass(frozen=True)
class WorkBuddySkillPackResult:
    zip_path: Path
    manifest_path: Path
    zip_sha256: str
    included_files: tuple[str, ...]
    manifest: dict


def package_workbuddy_skill(
    *,
    output_dir: str | Path,
    repo_workdir: str | Path | None = None,
) -> WorkBuddySkillPackResult:
    """Package the checked-in WorkBuddy Skill bundle as a deterministic zip."""

    repo = _resolve_repo(repo_workdir)
    source = repo / SKILL_SOURCE_RELATIVE
    _validate_source(source)

    output = Path(output_dir).expanduser().resolve()
    _validate_output_dir(output=output, source=source)
    output.mkdir(parents=True, exist_ok=True)
    version = __version__
    zip_path = output / f"briefloop-workbuddy-skill-v{version}.zip"
    manifest_path = output / f"briefloop-workbuddy-skill-v{version}.manifest.json"

    source_entries = _skill_source_entries(source)
    embedded_manifest = _manifest_payload(
        repo=repo,
        zip_path=zip_path,
        source_entries=source_entries,
        zip_sha256="",
        include_zip_sha=False,
    )
    embedded_bytes = _json_bytes(embedded_manifest)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in source_entries:
            _write_zip_entry(archive, entry["archive_path"], entry["source_path"].read_bytes())
        _write_zip_entry(archive, EMBEDDED_MANIFEST, embedded_bytes)

    zip_digest = _sha256_file(zip_path)
    sidecar_manifest = _manifest_payload(
        repo=repo,
        zip_path=zip_path,
        source_entries=source_entries,
        zip_sha256=zip_digest,
        include_zip_sha=True,
    )
    sidecar_manifest["included_files"].append(
        {
            "path": EMBEDDED_MANIFEST,
            "sha256": sha256(embedded_bytes).hexdigest(),
            "size": len(embedded_bytes),
        }
    )
    manifest_path.write_bytes(_json_bytes(sidecar_manifest))

    return WorkBuddySkillPackResult(
        zip_path=zip_path,
        manifest_path=manifest_path,
        zip_sha256=zip_digest,
        included_files=tuple(item["path"] for item in sidecar_manifest["included_files"]),
        manifest=sidecar_manifest,
    )


def validate_workbuddy_skill_pack(
    *,
    zip_path: str | Path,
    manifest_path: str | Path,
) -> list[str]:
    """Return validation errors for a generated WorkBuddy Skill package."""

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

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("manifest schema_version mismatch")
    if manifest.get("runtime_effect") != "packaging_only":
        errors.append("manifest runtime_effect must be packaging_only")
    expected_zip_sha = manifest.get("zip_sha256")
    actual_zip_sha = _sha256_file(zip_file)
    if expected_zip_sha != actual_zip_sha:
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
    except zipfile.BadZipFile as exc:
        errors.append(f"bad zip file: {exc}")
    return errors


def _resolve_repo(repo_workdir: str | Path | None) -> Path:
    if repo_workdir is not None:
        repo = Path(repo_workdir).expanduser().resolve()
    else:
        repo = _source_checkout_root()
    if not (repo / "pyproject.toml").exists():
        raise WorkBuddySkillPackError(
            f"BriefLoop source checkout not found: {repo}. "
            "Pass --repo-workdir for source-clone WorkBuddy Skill packaging."
        )
    return repo


def _source_checkout_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / SKILL_SOURCE_RELATIVE).exists():
            return parent
    raise WorkBuddySkillPackError(
        "WorkBuddy Skill source bundle is source-clone-only and was not found. "
        "Run from a BriefLoop source checkout or pass --repo-workdir."
    )


def _validate_source(source: Path) -> None:
    missing = [rel for rel in REQUIRED_SKILL_FILES if not (source / rel).is_file()]
    if missing:
        raise WorkBuddySkillPackError(f"missing required WorkBuddy Skill files: {missing}")


def _validate_output_dir(*, output: Path, source: Path) -> None:
    try:
        output.relative_to(source.resolve())
    except ValueError:
        return
    raise WorkBuddySkillPackError(
        "WorkBuddy Skill output directory must not be inside the skill source tree."
    )


def _skill_source_entries(source: Path) -> list[dict]:
    entries: list[dict] = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            rel = path.relative_to(source).as_posix()
            raise WorkBuddySkillPackError(f"forbidden WorkBuddy Skill symlink: {rel}")
        if not path.is_file():
            continue
        rel = path.relative_to(source).as_posix()
        if _is_forbidden_relpath(rel):
            raise WorkBuddySkillPackError(f"forbidden WorkBuddy Skill pack path: {rel}")
        archive_path = f"{PACKAGE_PREFIX}/{rel}"
        entries.append(
            {
                "source_path": path,
                "path": archive_path,
                "archive_path": archive_path,
                "sha256": _sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return entries


def _manifest_payload(
    *,
    repo: Path,
    zip_path: Path,
    source_entries: list[dict],
    zip_sha256: str,
    include_zip_sha: bool,
) -> dict:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "runtime_effect": "packaging_only",
        "briefloop_version": __version__,
        "skill_name": "briefloop-workbuddy",
        "source_root": SKILL_SOURCE_RELATIVE.as_posix(),
        "package_filename": zip_path.name,
        "generated_at": DETERMINISTIC_GENERATED_AT,
        "deterministic": True,
        "distribution_boundary": (
            "local_workbuddy_skill_zip_not_marketplace_ready_not_python_package_data"
        ),
        "non_goals": [
            "delivery_approval",
            "gate_authority",
            "marketplace_publication",
            "release_authority",
            "semantic_proof",
            "workbuddy_runtime_authority",
        ],
        "repo_root": repo.name,
        "included_files": [
            {
                "path": entry["archive_path"],
                "sha256": entry["sha256"],
                "size": entry["size"],
            }
            for entry in source_entries
        ],
    }
    if include_zip_sha:
        payload["zip_sha256"] = zip_sha256
    return payload


def _write_zip_entry(archive: zipfile.ZipFile, rel_path: str, data: bytes) -> None:
    info = zipfile.ZipInfo(rel_path, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_forbidden_relpath(rel_path: str) -> bool:
    parts = set(Path(rel_path).parts)
    if parts & FORBIDDEN_PATH_PARTS:
        return True
    return any(part.startswith(".") for part in parts)


def _validate_archive_path(rel: str, errors: list[str]) -> None:
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        errors.append(f"unsafe archive path: {rel}")
    if _is_forbidden_relpath(rel):
        errors.append(f"forbidden archive path: {rel}")
