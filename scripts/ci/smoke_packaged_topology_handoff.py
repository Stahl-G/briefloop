#!/usr/bin/env python3
"""Package-install smoke for topology-aware runtime handoff.

This script assumes the package has already been installed into the current
environment. It exercises the installed `multi-agent-brief` CLI only; it does
not import source-tree modules or install workspace runtime kits.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from importlib.resources import files
from pathlib import Path


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = ["multi-agent-brief", *args]
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout)
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(cmd)}")
    return result


def _copy_tree(src, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _copy_tree(item, target)
        elif item.is_file():
            target.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")


def _strict_contract_base(root: Path) -> Path:
    base = root / "strict-contract-base"
    base.mkdir()
    (base / "__init__.py").write_text("", encoding="utf-8")
    _copy_tree(files("multi_agent_brief").joinpath("configs"), base / "configs")
    policy = base / "configs" / "policy_packs" / "default.yaml"
    text = policy.read_text(encoding="utf-8")
    if "role_topology: default" not in text:
        raise SystemExit("default policy pack does not declare role_topology: default")
    policy.write_text(
        text.replace("role_topology: default", "role_topology: strict"),
        encoding="utf-8",
    )
    return base


def _init_workspace(path: Path) -> None:
    _run(
        "init",
        str(path),
        "--language",
        "en-US",
        "--company",
        "Topology Smoke",
        "--industry",
        "manufacturing",
        "--title",
        "Topology Smoke Brief",
        "--audience",
        "management",
        "--cadence",
        "weekly",
        "--source-profile",
        "conservative",
        "--web-search-mode",
        "disabled",
    )
    (path / "input" / "smoke.md").write_text(
        "- Manufacturing output improved after the latest capacity ramp.\n",
        encoding="utf-8",
    )


def _handoff(path: Path) -> dict[str, object]:
    return json.loads(
        (path / "output" / "intermediate" / "agent_handoff.json").read_text(
            encoding="utf-8"
        )
    )


def _protocol_stage(handoff: dict[str, object], stage_id: str) -> dict[str, object]:
    protocol = handoff.get("stage_completion_protocol")
    if not isinstance(protocol, dict):
        raise SystemExit("handoff missing stage_completion_protocol")
    stages = protocol.get("stages")
    if not isinstance(stages, list):
        raise SystemExit("stage_completion_protocol.stages is not a list")
    for stage in stages:
        if isinstance(stage, dict) and stage.get("stage_id") == stage_id:
            return stage
    raise SystemExit(f"stage missing from protocol: {stage_id}")


def _assert_default_handoff(path: Path) -> None:
    data = _handoff(path)
    prompt = str(data.get("prompt") or "")
    prompt_plain = prompt.replace("`", "")
    screener = _protocol_stage(data, "screener")
    satisfaction = screener.get("topology_satisfaction")
    if not isinstance(satisfaction, dict):
        raise SystemExit("screener topology_satisfaction missing")
    default = satisfaction.get("default")
    if not isinstance(default, dict) or default.get("satisfied_by") != "scout":
        raise SystemExit("default topology does not satisfy screener by scout")
    required = default.get("required_artifacts")
    required_ids = {
        item.get("artifact_id")
        for item in required
        if isinstance(item, dict)
    }
    if required_ids != {"candidate_claims", "screened_candidates"}:
        raise SystemExit(f"default topology required artifacts wrong: {required_ids}")
    forbidden = default.get("forbidden_replay_actions")
    if forbidden != ["delegate screener", "state stage-complete --stage screener"]:
        raise SystemExit(f"default topology forbidden replay actions wrong: {forbidden}")
    if "default: satisfied by scout" not in prompt:
        raise SystemExit("handoff prompt does not describe default topology satisfaction")
    if "do not call state stage-complete --stage screener" not in prompt_plain:
        raise SystemExit("handoff prompt does not forbid default screener stage-complete replay")
    if "do not replay delegate screener, state stage-complete --stage screener" not in prompt:
        raise SystemExit("handoff prompt does not describe forbidden screener replay actions")
    if "independent MUST produce (strict)" not in prompt:
        raise SystemExit("handoff prompt does not reserve independent screener for strict")


def _assert_strict_handoff(path: Path) -> None:
    data = _handoff(path)
    prompt = str(data.get("prompt") or "")
    screener = _protocol_stage(data, "screener")
    satisfaction = screener.get("topology_satisfaction")
    if not isinstance(satisfaction, dict) or "strict" in satisfaction:
        raise SystemExit("strict topology should not mark screener satisfied")
    independent = screener.get("independent_completion_topologies")
    if independent != ["strict"]:
        raise SystemExit(f"strict topology independent list wrong: {independent}")
    if "independent MUST produce (strict): screened_candidates" not in prompt:
        raise SystemExit("strict handoff does not require independent screened_candidates")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mabw-topology-smoke-") as tmp:
        root = Path(tmp)
        default_ws = root / "default"
        strict_ws = root / "strict"

        _init_workspace(default_ws)
        _run("run", "--workspace", str(default_ws), "--skip-doctor", cwd=Path("/tmp"))
        _assert_default_handoff(default_ws)

        _init_workspace(strict_ws)
        strict_contract_base = _strict_contract_base(root)
        _run(
            "run",
            "--workspace",
            str(strict_ws),
            "--repo-workdir",
            str(strict_contract_base),
            "--skip-doctor",
            cwd=Path("/tmp"),
        )
        _assert_strict_handoff(strict_ws)

    print("[OK] packaged topology handoff smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
