"""Solver/data-area isolation helpers (design-0223 §2 / protocol §6.4).

Design §2 promises: the solver runtime has no dataset credentials and no
permission on the full CSVs, with the boundary carried by the OS — not by
prompt text.  File permission bits (0700/0600) only fence off *other* OS
users; they are not a boundary against a solver process running as the same
user.  This module carries the code-enforced halves of that promise:

* ``solver_environment()`` builds the environment every solver process must
  be launched with: a copy of the ambient mapping with every dataset
  credential variable removed.  ``present_credentials()`` reports what the
  current process would leak, so the runner can fail closed instead of
  silently inheriting credentials into episode execution.
* ``boundary_report()`` reads ``config.json isolation.solver_boundary`` —
  the Q3-freezable description of a real OS-level boundary (a dedicated
  local user that owns the restricted data, or a sandbox profile denying
  reads on it).  It stays ``null`` until the freeze; the real-execution
  gate refuses to start without an enforced boundary, so same-user
  "permissions only" can never silently pass for isolation.
* ``audit_data_area()`` re-verifies the permission bits at run time, so
  drift (``gated/`` or ``evaluator-only/`` becoming group/world readable)
  fails closed instead of quietly widening access.

What this module deliberately does NOT claim: chmod alone is a same-user
boundary.  Creating the dedicated OS user or seatbelt profile is a one-time
admin action outside this repository; the code here only refuses to run
real episodes until such a boundary is configured and named in config.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Mapping

# Credentials that gate the restricted HF dataset download.  Only the data
# preparation process may hold these; solver/reviewer/learning processes
# must never see them (design §2, protocol §6.4).
CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HUGGING_FACE_TOKEN",
)

# Allowed mechanisms for the Q3 solver boundary.  ``detail`` must name the
# concrete artifact (user name / profile path) so the freeze record is
# auditable rather than a bare boolean promise.
_BOUNDARY_KINDS: tuple[str, ...] = ("dedicated-user", "sandbox")

_PRIVATE_DIRS = ("gated", "evaluator-only")
_SOLVER_VISIBLE_DIRS = ("question_only", "corpus", "episodes")


def _solver_visible_roots(data_root: Path) -> list[Path]:
    """Named solver-visible directories plus every staged ``corpus*`` variant.

    The V2 main-test corpus is ``corpus``; the dev-pilot v1 plain-text corpus
    stages beside it as ``corpus-v1``.  Both are inside the solver's read
    surface, so both belong in the symlink-containment audit.
    """
    roots = {data_root / name for name in _SOLVER_VISIBLE_DIRS}
    roots.update(path for path in data_root.glob("corpus*") if path.is_dir())
    return sorted(roots)


class IsolationError(RuntimeError):
    """Isolation audit failed closed."""


def present_credentials(env: Mapping[str, str] | None = None) -> list[str]:
    """Credential variables present in ``env`` (defaults to this process)."""
    source = os.environ if env is None else env
    return sorted(name for name in CREDENTIAL_ENV_VARS if source.get(name))


def solver_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment solver processes must be launched with.

    A plain copy of the ambient mapping minus every credential variable —
    the Q3 real transport has to pass exactly this mapping (never
    ``os.environ``) when spawning a solver host.
    """
    source = os.environ if env is None else env
    return {name: value for name, value in source.items()
            if name not in CREDENTIAL_ENV_VARS}


def _mode_bits(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def audit_data_area(data_root: Path) -> dict[str, Any]:
    """Re-verify the data-area permission bits and symlink containment.

    Green requires: both private directories exist with owner-only bits,
    every file under ``evaluator-only/gold`` is owner-only, and no symlink
    inside a solver-visible directory escapes the data root.  Anything else
    is reported red — callers fail closed on red.
    """
    data_root = Path(data_root).expanduser().resolve()
    failures: list[dict[str, str]] = []
    for name in _PRIVATE_DIRS:
        path = data_root / name
        if not path.is_dir():
            failures.append({"path": name, "error": "missing private directory"})
            continue
        mode = _mode_bits(path)
        if mode & 0o077:
            failures.append({"path": name, "error": f"mode {mode:o} grants group/world access"})
    gold_dir = data_root / "evaluator-only" / "gold"
    if gold_dir.is_dir():
        for file in sorted(gold_dir.rglob("*")):
            if file.is_file() and _mode_bits(file) & 0o177:
                failures.append({"path": str(file.relative_to(data_root)),
                                 "error": f"mode {_mode_bits(file):o} grants group/world access"})
    for root in _solver_visible_roots(data_root):
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for member in filenames:
                path = Path(dirpath) / member
                if path.is_symlink():
                    target = path.resolve()
                    if not str(target).startswith(str(data_root) + os.sep):
                        failures.append({"path": str(path.relative_to(data_root)),
                                         "error": f"symlink escapes the data area: {target}"})
    return {
        "schema_version": "officeqa.isolation_audit.v1",
        "data_root": str(data_root),
        "credential_env_vars": list(CREDENTIAL_ENV_VARS),
        "credentials_present_in_this_process": present_credentials(),
        "private_dirs": {name: oct(_mode_bits(data_root / name)) if (data_root / name).is_dir() else None
                         for name in _PRIVATE_DIRS},
        "failures": failures,
        "status": "green" if not failures else "red",
        "boundary_note": ("0700/0600 only fences other OS users; a same-user solver needs the "
                          "config isolation.solver_boundary (dedicated user or sandbox) before "
                          "any real episode runs"),
    }


def boundary_report(config: Mapping[str, Any]) -> dict[str, Any]:
    """Whether config names an enforced OS-level solver boundary (Q3 freeze)."""
    boundary = (config.get("isolation") or {}).get("solver_boundary")
    enforced = (isinstance(boundary, dict)
                and boundary.get("kind") in _BOUNDARY_KINDS
                and bool(str(boundary.get("detail") or "").strip()))
    return {
        "enforced": enforced,
        "configured": boundary,
        "allowed_kinds": list(_BOUNDARY_KINDS),
        "reason": (None if enforced else
                   "isolation.solver_boundary 未配置（null）：真实执行需要专用 OS 用户或沙箱承担同用户隔离，"
                   "文件权限位本身不构成边界"),
    }


def assert_real_run_isolation(config: Mapping[str, Any], data_root: Path) -> dict[str, Any]:
    """Fail closed unless every isolation precondition for real runs holds.

    Called by the real-execution gate: data-area audit green, solver
    environment free of credentials, and an enforced OS-level boundary
    named in config.
    """
    audit = audit_data_area(data_root)
    if audit["status"] != "green":
        raise IsolationError(f"data-area isolation audit failed: {audit['failures'][:5]}")
    boundary = boundary_report(config)
    if not boundary["enforced"]:
        raise IsolationError(str(boundary["reason"]))
    leaked = present_credentials(solver_environment())
    if leaked:  # defensive: solver_environment removes these by construction
        raise IsolationError(f"solver environment would still carry credentials: {leaked}")
    return {"data_area": audit, "boundary": boundary, "solver_environment_clean": True}
