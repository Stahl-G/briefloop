from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)


ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = ROOT / "src/multi_agent_brief"


def _contract_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    configs = repo / "configs"
    configs.mkdir(parents=True)
    for filename in ("stage_specs.yaml", "artifact_contracts.yaml"):
        shutil.copy2(ROOT / "configs" / filename, configs / filename)
    return repo


def _read(repo: Path, filename: str) -> dict[str, Any]:
    return yaml.safe_load((repo / "configs" / filename).read_text(encoding="utf-8"))


def _write(repo: Path, filename: str, payload: dict[str, Any]) -> None:
    (repo / "configs" / filename).write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def _file_observations(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _assert_rejected_without_writes(
    repo: Path,
    loader: Callable[[Path], list[dict[str, Any]]],
    *,
    field: str | None = None,
) -> RuntimeStateError:
    before = _file_observations(repo)
    with pytest.raises(RuntimeStateError) as exc_info:
        loader(repo)
    assert exc_info.value.error_code == E_TRANSACTION_INTEGRITY
    if field is not None:
        assert exc_info.value.details.get("field") == field
    assert _file_observations(repo) == before
    return exc_info.value


def _first_stage(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["workflow"]["stages"][0]


def _first_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["artifacts"][0]


def test_canonical_root_and_packaged_contract_pairs_are_equivalent() -> None:
    root_stages = load_stage_specs(ROOT)
    root_artifacts = load_artifact_contracts(ROOT)
    packaged_stages = load_stage_specs(PACKAGE_ROOT)
    packaged_artifacts = load_artifact_contracts(PACKAGE_ROOT)

    assert packaged_stages == root_stages
    assert packaged_artifacts == root_artifacts
    assert len(root_stages) == 10
    assert len(root_artifacts) == 30

    raw_stages = yaml.safe_load((ROOT / "configs/stage_specs.yaml").read_text(encoding="utf-8"))
    raw_artifacts = yaml.safe_load(
        (ROOT / "configs/artifact_contracts.yaml").read_text(encoding="utf-8")
    )
    assert root_stages == raw_stages["workflow"]["stages"]
    assert root_artifacts == raw_artifacts["artifacts"]


def test_unknown_extension_keys_survive_complete_pair_validation(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    stages = _read(repo, "stage_specs.yaml")
    artifacts = _read(repo, "artifact_contracts.yaml")
    _first_stage(stages)["extension"] = {"retained": True}
    _first_artifact(artifacts)["extension"] = {"retained": True}
    _write(repo, "stage_specs.yaml", stages)
    _write(repo, "artifact_contracts.yaml", artifacts)

    assert load_stage_specs(repo)[0]["extension"] == {"retained": True}
    assert load_artifact_contracts(repo)[0]["extension"] == {"retained": True}


def test_canonical_control_tool_classification_is_ratchet_locked() -> None:
    expected = [
        ("source_evidence_pack_manifest", "source-discovery"),
        ("evidence_extract_source_lock", "source-discovery"),
        ("evidence_extract_page_inventory", "source-discovery"),
        ("semantic_support_acceptance_ledger", "auditor"),
        ("human_approval_ledger", "finalize"),
        ("release_readiness_report", "finalize"),
        ("quality_panel", "quality-panel"),
        ("quality_summary", "quality-panel"),
        ("quality_panel_html", "quality-panel"),
        ("guidance_manifestation_report", "guidance-manifestation"),
        ("analyst_draft_snapshot", "analyst"),
        ("provenance_graph", "provenance"),
    ]

    for repo in (ROOT, PACKAGE_ROOT):
        assert [
            (artifact["artifact_id"], artifact["producer_stage"])
            for artifact in load_artifact_contracts(repo)
            if artifact.get("producer_kind") == "control_tool"
        ] == expected


@pytest.mark.parametrize(
    ("filename", "replacement"),
    [
        ("stage_specs.yaml", "[unterminated\n"),
        ("stage_specs.yaml", "- not-a-mapping\n"),
        ("artifact_contracts.yaml", "[unterminated\n"),
        ("artifact_contracts.yaml", "- not-a-mapping\n"),
    ],
    ids=[
        "stage-malformed-yaml",
        "stage-non-mapping-root",
        "artifact-malformed-yaml",
        "artifact-non-mapping-root",
    ],
)
@pytest.mark.parametrize("loader", [load_stage_specs, load_artifact_contracts])
def test_both_public_loaders_reject_malformed_contract_half_without_writes(
    tmp_path: Path,
    filename: str,
    replacement: str,
    loader: Callable[[Path], list[dict[str, Any]]],
) -> None:
    repo = _contract_repo(tmp_path)
    (repo / "configs" / filename).write_text(replacement, encoding="utf-8")

    _assert_rejected_without_writes(repo, loader)


@pytest.mark.parametrize("filename", ["stage_specs.yaml", "artifact_contracts.yaml"])
@pytest.mark.parametrize("loader", [load_stage_specs, load_artifact_contracts])
def test_both_public_loaders_reject_missing_contract_half_without_writes(
    tmp_path: Path,
    filename: str,
    loader: Callable[[Path], list[dict[str, Any]]],
) -> None:
    repo = _contract_repo(tmp_path)
    (repo / "configs" / filename).unlink()

    _assert_rejected_without_writes(repo, loader)


@pytest.mark.parametrize("filename", ["stage_specs.yaml", "artifact_contracts.yaml"])
def test_contract_loader_rejects_invalid_utf8_without_writes(
    tmp_path: Path,
    filename: str,
) -> None:
    repo = _contract_repo(tmp_path)
    (repo / "configs" / filename).write_bytes(b"\xff\xfe\x00")

    _assert_rejected_without_writes(repo, load_stage_specs)


@pytest.mark.parametrize("filename", ["stage_specs.yaml", "artifact_contracts.yaml"])
@pytest.mark.parametrize("loader", [load_stage_specs, load_artifact_contracts])
def test_contract_loader_rejects_duplicate_yaml_mapping_keys_without_writes(
    tmp_path: Path,
    filename: str,
    loader: Callable[[Path], list[dict[str, Any]]],
) -> None:
    repo = _contract_repo(tmp_path)
    path = repo / "configs" / filename
    path.write_text(
        path.read_text(encoding="utf-8") + "schema_version: duplicate-erasure\n",
        encoding="utf-8",
    )

    exc = _assert_rejected_without_writes(repo, loader)
    assert "duplicate key" in exc.details["reason"]


@pytest.mark.parametrize(
    ("filename", "schema"),
    [
        ("stage_specs.yaml", "multi-agent-brief-stage-specs/v0"),
        ("artifact_contracts.yaml", "multi-agent-brief-artifact-contracts/v0"),
    ],
)
def test_contract_loader_rejects_wrong_schema_without_writes(
    tmp_path: Path,
    filename: str,
    schema: str,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, filename)
    payload["schema_version"] = schema
    _write(repo, filename, payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts, field="schema_version")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow", []),
        ("workflow.stages", "doctor"),
        ("workflow.stages", []),
        ("workflow.stages[0]", "doctor"),
    ],
)
def test_stage_contract_rejects_container_or_cardinality_loss(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    if field == "workflow":
        payload["workflow"] = value
    elif field == "workflow.stages[0]":
        payload["workflow"]["stages"][0] = value
    else:
        payload["workflow"]["stages"] = value
    _write(repo, "stage_specs.yaml", payload)

    _assert_rejected_without_writes(repo, load_stage_specs)


@pytest.mark.parametrize(
    ("field", "value", "remove"),
    [
        ("orchestrator_role", None, True),
        ("orchestrator_role", 1, False),
        ("orchestrator_role", "", False),
        ("default_policy_pack", " default", False),
    ],
)
def test_stage_contract_rejects_invalid_workflow_strings(
    tmp_path: Path,
    field: str,
    value: Any,
    remove: bool,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    if remove:
        payload["workflow"].pop(field)
    else:
        payload["workflow"][field] = value
    _write(repo, "stage_specs.yaml", payload)

    _assert_rejected_without_writes(repo, load_stage_specs)


@pytest.mark.parametrize("field", ["stage_id", "owner", "category"])
@pytest.mark.parametrize("value", [None, 1, "", " padded"])
def test_stage_contract_rejects_invalid_required_stage_strings(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    _first_stage(payload)[field] = value
    _write(repo, "stage_specs.yaml", payload)

    _assert_rejected_without_writes(repo, load_stage_specs)


@pytest.mark.parametrize("value", [None, 1, "", " command "])
def test_stage_contract_rejects_invalid_present_command(
    tmp_path: Path,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    _first_stage(payload)["command"] = value
    _write(repo, "stage_specs.yaml", payload)

    _assert_rejected_without_writes(repo, load_stage_specs)


@pytest.mark.parametrize(
    "field",
    ["consumes", "produces", "expected_artifacts", "allowed_decisions"],
)
@pytest.mark.parametrize(
    ("value", "case_id"),
    [
        (None, "missing"),
        ("item", "scalar"),
        ([1], "non-string"),
        ([""], "blank"),
        (["duplicate", "duplicate"], "duplicate"),
    ],
    ids=lambda item: str(item),
)
def test_stage_contract_rejects_invalid_string_lists(
    tmp_path: Path,
    field: str,
    value: Any,
    case_id: str,
) -> None:
    del case_id
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    stage = _first_stage(payload)
    if value is None:
        stage.pop(field)
    else:
        stage[field] = value
    _write(repo, "stage_specs.yaml", payload)

    _assert_rejected_without_writes(repo, load_stage_specs)


def test_stage_contract_rejects_duplicate_stage_id_without_writes(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    payload["workflow"]["stages"][1]["stage_id"] = _first_stage(payload)["stage_id"]
    _write(repo, "stage_specs.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        ("container", "workflow.stages[4].topology_satisfaction"),
        ("key", "workflow.stages[4].topology_satisfaction"),
        ("rule", "workflow.stages[4].topology_satisfaction.default"),
        ("satisfied_by", "workflow.stages[4].topology_satisfaction.default.satisfied_by"),
        (
            "required_artifacts",
            "workflow.stages[4].topology_satisfaction.default.required_artifacts",
        ),
    ],
)
def test_stage_contract_rejects_malformed_topology(
    tmp_path: Path,
    mutation: str,
    field: str,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    stage = payload["workflow"]["stages"][4]
    if mutation == "container":
        stage["topology_satisfaction"] = []
    elif mutation == "key":
        stage["topology_satisfaction"] = {1: {"satisfied_by": "scout", "required_artifacts": []}}
    elif mutation == "rule":
        stage["topology_satisfaction"]["default"] = []
    elif mutation == "satisfied_by":
        stage["topology_satisfaction"]["default"]["satisfied_by"] = 1
    else:
        stage["topology_satisfaction"]["default"]["required_artifacts"] = "candidate_claims"
    _write(repo, "stage_specs.yaml", payload)

    _assert_rejected_without_writes(repo, load_stage_specs, field=field)


def test_topology_satisfied_by_is_a_role_namespace_not_a_stage_foreign_key(
    tmp_path: Path,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    stage_ids = {stage["stage_id"] for stage in payload["workflow"]["stages"]}
    writer_rules = [
        rule
        for stage in payload["workflow"]["stages"]
        for rule in stage.get("topology_satisfaction", {}).values()
        if rule["satisfied_by"] == "writer"
    ]

    assert "writer" not in stage_ids
    assert len(writer_rules) == 2
    assert load_stage_specs(repo)


@pytest.mark.parametrize("value", [None, 1, "", " writer"])
def test_topology_satisfied_by_rejects_non_string_blank_or_padded_values(
    tmp_path: Path,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    payload["workflow"]["stages"][4]["topology_satisfaction"]["default"][
        "satisfied_by"
    ] = value
    _write(repo, "stage_specs.yaml", payload)

    _assert_rejected_without_writes(repo, load_stage_specs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_contract", []),
        ("artifacts", "artifact"),
        ("artifacts", ["artifact"]),
    ],
)
def test_artifact_contract_rejects_container_or_cardinality_loss(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    payload[field] = value
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize(
    "field",
    ["status_values", "provenance_ready_fields", "producer_kind_values"],
)
@pytest.mark.parametrize("value", [None, "value", [1], [""], ["duplicate", "duplicate"]])
def test_artifact_contract_rejects_invalid_metadata_lists(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    metadata = payload["artifact_contract"]
    if value is None:
        metadata.pop(field)
    else:
        metadata[field] = value
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


def test_artifact_contract_requires_workflow_stage_producer_kind(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    payload["artifact_contract"]["producer_kind_values"] = ["control_tool"]
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize("loader", [load_stage_specs, load_artifact_contracts])
@pytest.mark.parametrize("used_by_artifact", [False, True], ids=["unused", "used"])
def test_v1_contract_rejects_declared_unsupported_producer_kind_without_writes(
    tmp_path: Path,
    loader: Callable[[Path], list[dict[str, Any]]],
    used_by_artifact: bool,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    payload["artifact_contract"]["producer_kind_values"].append("external-tool")
    if used_by_artifact:
        _first_artifact(payload)["producer_kind"] = "external-tool"
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(
        repo,
        loader,
        field="artifact_contract.producer_kind_values",
    )


@pytest.mark.parametrize("loader", [load_stage_specs, load_artifact_contracts])
@pytest.mark.parametrize(
    "alias",
    ["yes", "no", "on", "off", "Yes", "NO", "ON", "Off"],
)
def test_required_boolean_rejects_yaml_11_aliases_before_type_validation(
    tmp_path: Path,
    loader: Callable[[Path], list[dict[str, Any]]],
    alias: str,
) -> None:
    repo = _contract_repo(tmp_path)
    path = repo / "configs/artifact_contracts.yaml"
    text = path.read_text(encoding="utf-8")
    assert "required: false" in text
    path.write_text(
        text.replace("required: false", f"required: {alias}", 1),
        encoding="utf-8",
    )

    _assert_rejected_without_writes(
        repo,
        loader,
        field="artifacts[0].required",
    )


@pytest.mark.parametrize("loader", [load_stage_specs, load_artifact_contracts])
@pytest.mark.parametrize(
    "tagged_scalar",
    ["!!bool yes", "!!bool on", "!!bool TRUE", "!!bool 1", "!!int true"],
)
def test_explicit_yaml_scalar_tags_fail_closed_without_untyped_exceptions(
    tmp_path: Path,
    loader: Callable[[Path], list[dict[str, Any]]],
    tagged_scalar: str,
) -> None:
    repo = _contract_repo(tmp_path)
    path = repo / "configs/artifact_contracts.yaml"
    text = path.read_text(encoding="utf-8")
    assert "required: false" in text
    path.write_text(
        text.replace("required: false", f"required: {tagged_scalar}", 1),
        encoding="utf-8",
    )

    exc = _assert_rejected_without_writes(repo, loader)
    assert "Invalid YAML contract file" in str(exc)


@pytest.mark.parametrize("tagged_scalar", ["!!bool true", "!!bool false"])
def test_explicit_lowercase_yaml_boolean_tags_remain_canonical(
    tmp_path: Path,
    tagged_scalar: str,
) -> None:
    repo = _contract_repo(tmp_path)
    path = repo / "configs/artifact_contracts.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("required: false", f"required: {tagged_scalar}", 1),
        encoding="utf-8",
    )

    required = load_artifact_contracts(repo)[0]["required"]
    assert required is (tagged_scalar == "!!bool true")


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ([], "artifact_contract.edge_direction_notes"),
        ({1: "note"}, "artifact_contract.edge_direction_notes"),
        ({"edge": 1}, "artifact_contract.edge_direction_notes.edge"),
    ],
)
def test_artifact_contract_rejects_invalid_edge_direction_notes(
    tmp_path: Path,
    value: Any,
    field: str,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    payload["artifact_contract"]["edge_direction_notes"] = value
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts, field=field)


@pytest.mark.parametrize(
    "field",
    [
        "artifact_id",
        "path",
        "format",
        "producer_stage",
        "producer_role",
        "validation_result",
        "retry_or_human_review_decision",
    ],
)
@pytest.mark.parametrize("value", [None, 1, "", " padded"])
def test_artifact_contract_rejects_invalid_required_strings(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    _first_artifact(payload)[field] = value
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize("value", [None, 1, [], {}])
def test_artifact_contract_rejects_non_string_blocking_reason(
    tmp_path: Path,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    _first_artifact(payload)["blocking_reason"] = value
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize("value", [1, 0, 1.0, "true", None])
def test_artifact_required_is_exact_boolean(tmp_path: Path, value: Any) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    _first_artifact(payload)["required"] = value
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_stage_specs)


@pytest.mark.parametrize("field", ["consumer_stages", "allowed_decisions"])
@pytest.mark.parametrize("value", [None, "value", [1], [""], ["duplicate", "duplicate"]])
def test_artifact_contract_rejects_invalid_string_lists(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    artifact = _first_artifact(payload)
    if value is None:
        artifact.pop(field)
    else:
        artifact[field] = value
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


def test_artifact_contract_rejects_duplicate_artifact_id(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    payload["artifacts"][1]["artifact_id"] = _first_artifact(payload)["artifact_id"]
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize("artifact_format", ["", "txt", "JSON", 1])
def test_artifact_contract_rejects_unsupported_format(
    tmp_path: Path,
    artifact_format: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    _first_artifact(payload)["format"] = artifact_format
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


def test_artifact_retry_decision_must_be_allowed(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    _first_artifact(payload)["retry_or_human_review_decision"] = "not-allowed"
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize("producer_kind", [1, "", " control_tool", "unknown"])
def test_artifact_contract_rejects_invalid_producer_kind(
    tmp_path: Path,
    producer_kind: Any,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    _first_artifact(payload)["producer_kind"] = producer_kind
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


def test_absent_producer_kind_defaults_to_known_workflow_stage(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    artifacts = load_artifact_contracts(repo)

    assert "producer_kind" not in artifacts[0]
    assert artifacts[0]["producer_stage"] == "source-discovery"


@pytest.mark.parametrize("explicit", [False, True])
def test_workflow_producer_kind_rejects_unknown_stage(
    tmp_path: Path,
    explicit: bool,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    artifact = _first_artifact(payload)
    artifact["producer_stage"] = "unknown-stage"
    if explicit:
        artifact["producer_kind"] = "workflow_stage"
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize("producer_stage", ["doctor", "quality-panel", "provenance"])
def test_control_tool_accepts_canonical_stage_or_external_owner_namespace(
    tmp_path: Path,
    producer_stage: str,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    artifact = next(
        item for item in payload["artifacts"] if item["artifact_id"] == "quality_panel"
    )
    artifact["producer_stage"] = producer_stage
    _write(repo, "artifact_contracts.yaml", payload)

    loaded = {
        item["artifact_id"]: item for item in load_artifact_contracts(repo)
    }
    assert loaded["quality_panel"]["producer_stage"] == producer_stage


@pytest.mark.parametrize(
    "producer_stage",
    ["", " Quality-Panel", "Quality-Panel", "quality_panel", "quality/panel", "quality--panel"],
)
def test_control_tool_rejects_noncanonical_owner_namespace(
    tmp_path: Path,
    producer_stage: str,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    artifact = _first_artifact(payload)
    artifact["producer_kind"] = "control_tool"
    artifact["producer_stage"] = producer_stage
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


def test_control_tool_consumer_stages_still_bind_workflow_stages(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    artifact = _first_artifact(payload)
    artifact["producer_kind"] = "control_tool"
    artifact["producer_stage"] = "quality-panel"
    artifact["consumer_stages"] = ["missing-stage"]
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("stage-produces", "unknown artifact"),
        ("stage-expected", "unknown artifact"),
        ("topology-required", "unknown artifact"),
        ("artifact-consumer", "unknown stage"),
    ],
)
def test_runtime_contract_pair_rejects_unknown_cross_references(
    tmp_path: Path,
    mutation: str,
    expected_fragment: str,
) -> None:
    repo = _contract_repo(tmp_path)
    stages = _read(repo, "stage_specs.yaml")
    artifacts = _read(repo, "artifact_contracts.yaml")
    if mutation == "stage-produces":
        _first_stage(stages)["produces"] = ["missing-artifact"]
    elif mutation == "stage-expected":
        _first_stage(stages)["expected_artifacts"] = ["missing-artifact"]
    elif mutation == "topology-required":
        stages["workflow"]["stages"][4]["topology_satisfaction"]["default"][
            "required_artifacts"
        ] = ["missing-artifact"]
    else:
        _first_artifact(artifacts)["consumer_stages"] = ["missing-stage"]
    _write(repo, "stage_specs.yaml", stages)
    _write(repo, "artifact_contracts.yaml", artifacts)

    exc = _assert_rejected_without_writes(repo, load_stage_specs)
    assert expected_fragment in str(exc)


@pytest.mark.parametrize("relation", ["produces", "expected_artifacts"])
@pytest.mark.parametrize("loader", [load_stage_specs, load_artifact_contracts])
def test_stage_declared_artifact_owner_must_match_artifact_producer_stage(
    tmp_path: Path,
    relation: str,
    loader: Callable[[Path], list[dict[str, Any]]],
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    payload["workflow"]["stages"][0][relation] = ["audit_report"]
    _write(repo, "stage_specs.yaml", payload)

    exc = _assert_rejected_without_writes(
        repo,
        loader,
        field=f"workflow.stages[0].{relation}[0]",
    )
    assert "ownership does not match artifact producer_stage" in str(exc)


def test_canonical_stage_declared_artifact_ownership_relations_remain_valid() -> None:
    stages = load_stage_specs(ROOT)
    artifacts = {artifact["artifact_id"]: artifact for artifact in load_artifact_contracts(ROOT)}
    source_discovery = next(stage for stage in stages if stage["stage_id"] == "source-discovery")
    analyst = next(stage for stage in stages if stage["stage_id"] == "analyst")

    assert source_discovery["produces"] == ["source_candidates"]
    assert artifacts["source_candidates"].get("producer_kind", "workflow_stage") == "workflow_stage"
    assert artifacts["source_candidates"]["producer_stage"] == "source-discovery"
    assert analyst["produces"] == ["analyst_draft_snapshot"]
    assert artifacts["analyst_draft_snapshot"]["producer_kind"] == "control_tool"
    assert artifacts["analyst_draft_snapshot"]["producer_stage"] == "analyst"


def test_unlisted_external_control_owner_namespaces_remain_valid() -> None:
    stages = load_stage_specs(ROOT)
    artifacts = load_artifact_contracts(ROOT)
    declared = {
        artifact_id
        for stage in stages
        for field in ("produces", "expected_artifacts")
        for artifact_id in stage[field]
    }
    external = {
        artifact["producer_stage"]
        for artifact in artifacts
        if artifact.get("producer_kind") == "control_tool"
        and artifact["artifact_id"] not in declared
        and artifact["producer_stage"] not in {stage["stage_id"] for stage in stages}
    }

    assert external == {"guidance-manifestation", "provenance", "quality-panel"}


def test_stage_consumes_may_reference_external_runtime_inputs(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "stage_specs.yaml")
    _first_stage(payload)["consumes"] = ["external-runtime-input"]
    _write(repo, "stage_specs.yaml", payload)

    assert load_stage_specs(repo)[0]["consumes"] == ["external-runtime-input"]


def test_empty_artifact_universe_is_structurally_legal_when_unreferenced(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    stages = _read(repo, "stage_specs.yaml")
    for stage in stages["workflow"]["stages"]:
        stage["produces"] = []
        stage["expected_artifacts"] = []
        for rule in stage.get("topology_satisfaction", {}).values():
            rule["required_artifacts"] = []
    artifacts = _read(repo, "artifact_contracts.yaml")
    artifacts["artifacts"] = []
    _write(repo, "stage_specs.yaml", stages)
    _write(repo, "artifact_contracts.yaml", artifacts)

    assert load_artifact_contracts(repo) == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("custom/./artifact.json", "custom/artifact.json"),
        (r"custom\artifact.json", "custom/artifact.json"),
    ],
)
def test_artifact_path_uses_existing_canonical_normalization(
    tmp_path: Path,
    path: str,
    expected: str,
) -> None:
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    _first_artifact(payload)["path"] = path
    _write(repo, "artifact_contracts.yaml", payload)

    assert load_artifact_contracts(repo)[0]["path"] == expected


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("reserved", "output/intermediate/WORKFLOW_STATE.json"),
        ("duplicate", "output/input_classification.json"),
        ("casefold-duplicate", "OUTPUT/INPUT_CLASSIFICATION.JSON"),
    ],
)
def test_artifact_paths_reject_reserved_or_duplicate_ownership(
    tmp_path: Path,
    mutation: str,
    value: str,
) -> None:
    del mutation
    repo = _contract_repo(tmp_path)
    payload = _read(repo, "artifact_contracts.yaml")
    _first_artifact(payload)["path"] = value
    _write(repo, "artifact_contracts.yaml", payload)

    _assert_rejected_without_writes(repo, load_artifact_contracts)


def test_contract_loader_rejects_invalid_repo_selector_as_typed_failure(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to(loop)

    with pytest.raises(RuntimeStateError) as exc_info:
        load_stage_specs(loop)

    assert exc_info.value.error_code == E_TRANSACTION_INTEGRITY
    assert exc_info.value.details["repo_workdir_type"] == "PosixPath"
