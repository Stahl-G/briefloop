"""Shared Orchestrator contract constants for runtime entrypoints."""

from __future__ import annotations

from pathlib import Path


CONTRACT_REFERENCES = {
    "orchestrator_contract": "configs/orchestrator_contract.yaml",
    "stage_specs": "configs/stage_specs.yaml",
    "artifact_contracts": "configs/artifact_contracts.yaml",
    "default_policy_pack": "configs/policy_packs/default.yaml",
}

RUNTIME_HERMES = "hermes"
RUNTIME_CLAUDE = "claude"
RUNTIME_OPENCODE = "opencode"
RUNTIME_CODEX = "codex"
RUNTIME_CODEBUDDY = "codebuddy"
RUNTIME_OPERATOR = "operator"
VALID_RUNTIMES = (
    RUNTIME_HERMES,
    RUNTIME_CLAUDE,
    RUNTIME_OPENCODE,
    RUNTIME_CODEX,
    RUNTIME_CODEBUDDY,
    RUNTIME_OPERATOR,
)
RUNTIME_CLI_CHOICE_PLACEHOLDER = "<" + "|".join(VALID_RUNTIMES) + ">"
HISTORICAL_READ_ONLY_RUNTIMES = frozenset({"auto", "controls", "manual"})


def require_canonical_runtime(runtime: object) -> str:
    """Return one exact runtime identity or reject non-canonical input."""
    if type(runtime) is not str or runtime not in VALID_RUNTIMES:
        raise ValueError(
            "Runtime identity must be one of: " + ", ".join(VALID_RUNTIMES)
        )
    return runtime

DECISION_VOCABULARY = (
    "continue",
    "retry_stage",
    "delegate_repair",
    "request_human_review",
    "block_run",
    "finalize",
)

ORCHESTRATOR_LOOP = (
    "Read workspace context -> read contract references -> identify the next stage -> "
    "delegate a specialist or Python tool -> check the expected artifact -> decide "
    f"{' / '.join(DECISION_VOCABULARY)}."
)


def contract_reference_bullets() -> str:
    return "\n".join(f"- {path}" for path in CONTRACT_REFERENCES.values())


def contract_references_exist(repo_workdir: str | Path) -> bool:
    repo = Path(repo_workdir).expanduser().resolve()
    return all((repo / rel_path).exists() for rel_path in CONTRACT_REFERENCES.values())


def is_source_repo(repo_workdir: str | Path) -> bool:
    repo = Path(repo_workdir).expanduser().resolve()
    return (
        (repo / "pyproject.toml").exists()
        and (repo / "src" / "multi_agent_brief").exists()
        and contract_references_exist(repo)
    )


def is_package_contract_base(contract_base: str | Path) -> bool:
    base = Path(contract_base).expanduser().resolve()
    return (base / "__init__.py").exists() and contract_references_exist(base)


def _candidate_parents(start: Path) -> list[Path]:
    resolved = start.expanduser().resolve()
    return [resolved, *resolved.parents]


def resolve_repo_workdir(
    repo_workdir: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
) -> Path:
    """Resolve the source repo that owns the shared Orchestrator contracts."""
    starts: list[Path] = []
    if repo_workdir is not None:
        starts.append(Path(repo_workdir))
    else:
        starts.append(Path.cwd())
        if workspace is not None:
            starts.append(Path(workspace))

        package_path = Path(__file__).resolve()
        starts.extend(package_path.parents)

    candidates: list[Path] = []
    seen: set[Path] = set()
    for start in starts:
        for candidate in _candidate_parents(start):
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
            if is_source_repo(candidate):
                return candidate

    for candidate in candidates:
        if is_package_contract_base(candidate):
            return candidate

    checked = ", ".join(str(path.expanduser().resolve()) for path in starts)
    raise ValueError(
        "Could not resolve MABW Orchestrator contract files. "
        f"Checked: {checked}. Pass --repo-workdir pointing to the source repository root, "
        "or install a package build that includes multi_agent_brief/configs/."
    )
