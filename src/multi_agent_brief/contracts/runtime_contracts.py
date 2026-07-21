"""Runtime contract loading: stage specs and artifact contracts.

Single source of truth (LD2-2b relocation). The legacy
``orchestrator.runtime_state.contracts_loader`` path is a re-export shim
until LD2-3. This module must not import from the runtime_state stack.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import yaml

from multi_agent_brief.contracts.runtime_errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)
from multi_agent_brief.contracts.artifact_paths import (
    validate_workspace_relative_artifact_path,
)
from multi_agent_brief.orchestrator_contract import CONTRACT_REFERENCES


RUNTIME_STATE_FILES = {
    "runtime_manifest": "output/intermediate/runtime_manifest.json",
    "workflow_state": "output/intermediate/workflow_state.json",
    "artifact_registry": "output/intermediate/artifact_registry.json",
    "event_log": "output/intermediate/event_log.jsonl",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuntimeStateError(
            f"Invalid YAML contract file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read contract file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeStateError(
            f"Contract file must contain a mapping: {path}",
            details={"path": str(path)},
        )
    return data


_STAGE_SPECS_SCHEMA = "multi-agent-brief-stage-specs/v1"
_ARTIFACT_CONTRACTS_SCHEMA = "multi-agent-brief-artifact-contracts/v1"
_SUPPORTED_ARTIFACT_FORMATS = frozenset({"html", "json", "markdown", "yaml"})
_SUPPORTED_PRODUCER_KINDS = ("workflow_stage", "control_tool")
_CONTROL_OWNER_NAMESPACE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class _RuntimeContractUniverse:
    """One completely validated runtime contract pair."""

    stages: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ValidatedRuntimeContractPayloads:
    """Validated detached payloads shared by file and Store-backed callers."""

    stage_specs: dict[str, Any]
    artifact_contracts: dict[str, Any]
    policy_pack: dict[str, Any]
    stages: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that preserves mapping cardinality by rejecting duplicates."""


_UniqueKeySafeLoader.yaml_implicit_resolvers = {
    first: [
        resolver
        for resolver in resolvers
        if resolver[0] != "tag:yaml.org,2002:bool"
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_UniqueKeySafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$"),
    list("tf"),
)


def _construct_exact_boolean(
    _loader: yaml.SafeLoader,
    node: yaml.ScalarNode,
) -> bool:
    if node.value not in {"true", "false"}:
        raise yaml.constructor.ConstructorError(
            "while constructing a contract boolean",
            node.start_mark,
            "boolean values must use lowercase true or false",
            node.start_mark,
        )
    return node.value == "true"


_UniqueKeySafeLoader.add_constructor(
    "tag:yaml.org,2002:bool",
    _construct_exact_boolean,
)


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a contract mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a contract mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _integrity_error(
    message: str,
    *,
    contract: str,
    field: str,
    **details: Any,
) -> NoReturn:
    raise RuntimeStateError(
        message,
        details={"contract": contract, "field": field, **details},
        error_code=E_TRANSACTION_INTEGRITY,
    )


def _resolve_repo_workdir(repo_workdir: str | Path) -> Path:
    try:
        return Path(repo_workdir).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeStateError(
            "Runtime contract repository path is invalid.",
            details={
                "repo_workdir_type": type(repo_workdir).__name__,
                "reason": str(exc),
            },
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc


def _contract_file(repo_workdir: Path, rel_path: str) -> Path:
    path = repo_workdir / rel_path
    if not path.is_file():
        raise RuntimeStateError(
            f"Contract file not found: {path}",
            details={"contract": rel_path, "repo_workdir": str(repo_workdir)},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return path


def _load_contract_yaml(repo_workdir: Path, contract_key: str) -> dict[str, Any]:
    rel_path = CONTRACT_REFERENCES[contract_key]
    path = _contract_file(repo_workdir, rel_path)
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read contract file: {path}",
            details={"contract": rel_path, "path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    except Exception as exc:
        raise RuntimeStateError(
            f"Invalid YAML contract file: {path}",
            details={"contract": rel_path, "path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    if type(data) is not dict:
        _integrity_error(
            f"Contract file must contain a mapping: {path}",
            contract=rel_path,
            field="<root>",
            value_type=type(data).__name__,
        )
    return data


def _required_mapping(
    payload: dict[str, Any],
    key: str,
    *,
    contract: str,
    field: str,
) -> dict[str, Any]:
    value = payload.get(key)
    if type(value) is not dict:
        _integrity_error(
            f"{field} must be a mapping.",
            contract=contract,
            field=field,
            value_type=type(value).__name__,
        )
    return value


def _required_list(
    payload: dict[str, Any],
    key: str,
    *,
    contract: str,
    field: str,
) -> list[Any]:
    value = payload.get(key)
    if type(value) is not list:
        _integrity_error(
            f"{field} must be a list.",
            contract=contract,
            field=field,
            value_type=type(value).__name__,
        )
    return value


def _required_string(
    payload: dict[str, Any],
    key: str,
    *,
    contract: str,
    field: str,
) -> str:
    value = payload.get(key)
    if type(value) is not str or not value or value != value.strip():
        _integrity_error(
            f"{field} must be an exact non-empty string without surrounding whitespace.",
            contract=contract,
            field=field,
            value_type=type(value).__name__,
        )
    return value


def _optional_string(
    payload: dict[str, Any],
    key: str,
    *,
    contract: str,
    field: str,
) -> str | None:
    if key not in payload:
        return None
    return _required_string(payload, key, contract=contract, field=field)


def _required_string_list(
    payload: dict[str, Any],
    key: str,
    *,
    contract: str,
    field: str,
) -> list[str]:
    value = _required_list(payload, key, contract=contract, field=field)
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if type(item) is not str or not item or item != item.strip():
            _integrity_error(
                f"{item_field} must be an exact non-empty string without surrounding whitespace.",
                contract=contract,
                field=item_field,
                value_type=type(item).__name__,
            )
        if item in seen:
            _integrity_error(
                f"{field} must not contain duplicate entries.",
                contract=contract,
                field=field,
                duplicate=item,
            )
        seen.add(item)
    return value


def _validate_stage_specs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    contract = CONTRACT_REFERENCES["stage_specs"]
    schema_version = payload.get("schema_version")
    if type(schema_version) is not str or schema_version != _STAGE_SPECS_SCHEMA:
        _integrity_error(
            "stage_specs.yaml schema_version is invalid.",
            contract=contract,
            field="schema_version",
            expected=_STAGE_SPECS_SCHEMA,
        )
    workflow = _required_mapping(
        payload,
        "workflow",
        contract=contract,
        field="workflow",
    )
    _required_string(
        workflow,
        "orchestrator_role",
        contract=contract,
        field="workflow.orchestrator_role",
    )
    _required_string(
        workflow,
        "default_policy_pack",
        contract=contract,
        field="workflow.default_policy_pack",
    )
    raw_stages = _required_list(
        workflow,
        "stages",
        contract=contract,
        field="workflow.stages",
    )
    if not raw_stages:
        _integrity_error(
            "workflow.stages must contain at least one stage.",
            contract=contract,
            field="workflow.stages",
        )

    stages: list[dict[str, Any]] = []
    stage_ids: set[str] = set()
    for index, raw_stage in enumerate(raw_stages):
        stage_field = f"workflow.stages[{index}]"
        if type(raw_stage) is not dict:
            _integrity_error(
                f"{stage_field} must be a mapping.",
                contract=contract,
                field=stage_field,
                value_type=type(raw_stage).__name__,
            )
        stage = raw_stage
        stage_id = _required_string(
            stage,
            "stage_id",
            contract=contract,
            field=f"{stage_field}.stage_id",
        )
        if stage_id in stage_ids:
            _integrity_error(
                "workflow stage_id values must be unique.",
                contract=contract,
                field=f"{stage_field}.stage_id",
                duplicate=stage_id,
            )
        stage_ids.add(stage_id)
        for key in ("owner", "category"):
            _required_string(
                stage,
                key,
                contract=contract,
                field=f"{stage_field}.{key}",
            )
        _optional_string(
            stage,
            "command",
            contract=contract,
            field=f"{stage_field}.command",
        )
        for key in ("consumes", "produces", "expected_artifacts", "allowed_decisions"):
            _required_string_list(
                stage,
                key,
                contract=contract,
                field=f"{stage_field}.{key}",
            )
        if "topology_satisfaction" in stage:
            topology = _required_mapping(
                stage,
                "topology_satisfaction",
                contract=contract,
                field=f"{stage_field}.topology_satisfaction",
            )
            for topology_key, raw_rule in topology.items():
                topology_field = f"{stage_field}.topology_satisfaction"
                if (
                    type(topology_key) is not str
                    or not topology_key
                    or topology_key != topology_key.strip()
                ):
                    _integrity_error(
                        "Topology keys must be exact non-empty strings without surrounding whitespace.",
                        contract=contract,
                        field=topology_field,
                        key_type=type(topology_key).__name__,
                    )
                rule_field = f"{topology_field}.{topology_key}"
                if type(raw_rule) is not dict:
                    _integrity_error(
                        f"{rule_field} must be a mapping.",
                        contract=contract,
                        field=rule_field,
                        value_type=type(raw_rule).__name__,
                    )
                _required_string(
                    raw_rule,
                    "satisfied_by",
                    contract=contract,
                    field=f"{rule_field}.satisfied_by",
                )
                _required_string_list(
                    raw_rule,
                    "required_artifacts",
                    contract=contract,
                    field=f"{rule_field}.required_artifacts",
                )
        stages.append(stage)
    return stages


def _validate_artifact_contracts(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    contract = CONTRACT_REFERENCES["artifact_contracts"]
    schema_version = payload.get("schema_version")
    if type(schema_version) is not str or schema_version != _ARTIFACT_CONTRACTS_SCHEMA:
        _integrity_error(
            "artifact_contracts.yaml schema_version is invalid.",
            contract=contract,
            field="schema_version",
            expected=_ARTIFACT_CONTRACTS_SCHEMA,
        )
    metadata = _required_mapping(
        payload,
        "artifact_contract",
        contract=contract,
        field="artifact_contract",
    )
    for key in ("status_values", "provenance_ready_fields", "producer_kind_values"):
        _required_string_list(
            metadata,
            key,
            contract=contract,
            field=f"artifact_contract.{key}",
        )
    producer_kind_values = metadata["producer_kind_values"]
    if producer_kind_values != list(_SUPPORTED_PRODUCER_KINDS):
        _integrity_error(
            "artifact_contract.producer_kind_values must exactly match the v1 runtime vocabulary.",
            contract=contract,
            field="artifact_contract.producer_kind_values",
            expected=list(_SUPPORTED_PRODUCER_KINDS),
        )
    edge_notes = _required_mapping(
        metadata,
        "edge_direction_notes",
        contract=contract,
        field="artifact_contract.edge_direction_notes",
    )
    for key, value in edge_notes.items():
        if type(key) is not str or not key or key != key.strip():
            _integrity_error(
                "artifact_contract.edge_direction_notes keys must be exact non-empty strings.",
                contract=contract,
                field="artifact_contract.edge_direction_notes",
                key_type=type(key).__name__,
            )
        if type(value) is not str:
            _integrity_error(
                "artifact_contract.edge_direction_notes values must be strings.",
                contract=contract,
                field=f"artifact_contract.edge_direction_notes.{key}",
                value_type=type(value).__name__,
            )

    raw_artifacts = _required_list(
        payload,
        "artifacts",
        contract=contract,
        field="artifacts",
    )
    artifacts: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    effective_producer_kinds: dict[str, str] = {}
    owners: dict[str, tuple[str, str]] = {}
    reserved = {str(path).casefold(): str(path) for path in RUNTIME_STATE_FILES.values()}
    for index, raw_artifact in enumerate(raw_artifacts):
        artifact_field = f"artifacts[{index}]"
        if type(raw_artifact) is not dict:
            _integrity_error(
                f"{artifact_field} must be a mapping.",
                contract=contract,
                field=artifact_field,
                value_type=type(raw_artifact).__name__,
            )
        artifact = raw_artifact
        artifact_id = _required_string(
            artifact,
            "artifact_id",
            contract=contract,
            field=f"{artifact_field}.artifact_id",
        )
        if artifact_id in artifact_ids:
            _integrity_error(
                "Artifact contract artifact_id values must be unique.",
                contract=contract,
                field=f"{artifact_field}.artifact_id",
                duplicate=artifact_id,
            )
        artifact_ids.add(artifact_id)
        for key in (
            "path",
            "format",
            "producer_stage",
            "producer_role",
            "validation_result",
            "retry_or_human_review_decision",
        ):
            _required_string(
                artifact,
                key,
                contract=contract,
                field=f"{artifact_field}.{key}",
            )
        blocking_reason = artifact.get("blocking_reason")
        if type(blocking_reason) is not str:
            _integrity_error(
                f"{artifact_field}.blocking_reason must be a string.",
                contract=contract,
                field=f"{artifact_field}.blocking_reason",
                value_type=type(blocking_reason).__name__,
            )
        required = artifact.get("required")
        if type(required) is not bool:
            _integrity_error(
                f"{artifact_field}.required must be a boolean.",
                contract=contract,
                field=f"{artifact_field}.required",
                value_type=type(required).__name__,
            )
        for key in ("consumer_stages", "allowed_decisions"):
            _required_string_list(
                artifact,
                key,
                contract=contract,
                field=f"{artifact_field}.{key}",
            )
        artifact_format = artifact["format"]
        if artifact_format not in _SUPPORTED_ARTIFACT_FORMATS:
            _integrity_error(
                f"{artifact_field}.format is unsupported.",
                contract=contract,
                field=f"{artifact_field}.format",
                value=artifact_format,
            )
        decision = artifact["retry_or_human_review_decision"]
        if decision not in artifact["allowed_decisions"]:
            _integrity_error(
                f"{artifact_field}.retry_or_human_review_decision must be present in allowed_decisions.",
                contract=contract,
                field=f"{artifact_field}.retry_or_human_review_decision",
                value=decision,
            )
        producer_kind = artifact.get("producer_kind", "workflow_stage")
        if type(producer_kind) is not str or not producer_kind or producer_kind != producer_kind.strip():
            _integrity_error(
                f"{artifact_field}.producer_kind must be an exact non-empty string.",
                contract=contract,
                field=f"{artifact_field}.producer_kind",
                value_type=type(producer_kind).__name__,
            )
        if producer_kind not in producer_kind_values:
            _integrity_error(
                f"{artifact_field}.producer_kind is not declared by the contract.",
                contract=contract,
                field=f"{artifact_field}.producer_kind",
                value=producer_kind,
            )
        effective_producer_kinds[artifact_id] = producer_kind

        canonical_path = validate_workspace_relative_artifact_path(
            artifact["path"],
            artifact_id=artifact_id,
            binding_source="artifact_contract",
        )
        artifact["path"] = canonical_path
        identity_key = canonical_path.casefold()
        if identity_key in reserved:
            _integrity_error(
                "Workflow artifact path conflicts with a runtime control file.",
                contract=contract,
                field=f"{artifact_field}.path",
                artifact_id=artifact_id,
                path=canonical_path,
                reserved_path=reserved[identity_key],
            )
        existing_owner = owners.get(identity_key)
        if existing_owner is not None:
            _integrity_error(
                "Canonical workflow artifact path must have exactly one owner.",
                contract=contract,
                field=f"{artifact_field}.path",
                path=canonical_path,
                existing_path=existing_owner[0],
                artifact_ids=[existing_owner[1], artifact_id],
            )
        owners[identity_key] = (canonical_path, artifact_id)
        artifacts.append(artifact)
    return artifacts, effective_producer_kinds


def _validate_local_coherence(
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    effective_producer_kinds: dict[str, str],
) -> None:
    contract = "runtime_contract_universe"
    stage_ids = {stage["stage_id"] for stage in stages}
    artifact_ids = {artifact["artifact_id"] for artifact in artifacts}
    artifacts_by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    for stage_index, stage in enumerate(stages):
        for field in ("produces", "expected_artifacts"):
            for item_index, artifact_id in enumerate(stage[field]):
                if artifact_id not in artifact_ids:
                    _integrity_error(
                        f"Stage {field} references an unknown artifact.",
                        contract=contract,
                        field=f"workflow.stages[{stage_index}].{field}[{item_index}]",
                        artifact_id=artifact_id,
                    )
                producer_stage = artifacts_by_id[artifact_id]["producer_stage"]
                if producer_stage != stage["stage_id"]:
                    _integrity_error(
                        f"Stage {field} ownership does not match artifact producer_stage.",
                        contract=contract,
                        field=f"workflow.stages[{stage_index}].{field}[{item_index}]",
                        stage_id=stage["stage_id"],
                        artifact_id=artifact_id,
                        producer_stage=producer_stage,
                    )
        topology = stage.get("topology_satisfaction", {})
        for topology_key, rule in topology.items():
            for item_index, artifact_id in enumerate(rule["required_artifacts"]):
                if artifact_id not in artifact_ids:
                    _integrity_error(
                        "Topology required_artifacts references an unknown artifact.",
                        contract=contract,
                        field=(
                            f"workflow.stages[{stage_index}].topology_satisfaction."
                            f"{topology_key}.required_artifacts[{item_index}]"
                        ),
                        artifact_id=artifact_id,
                    )

    for artifact_index, artifact in enumerate(artifacts):
        artifact_id = artifact["artifact_id"]
        producer_stage = artifact["producer_stage"]
        producer_kind = effective_producer_kinds[artifact_id]
        if producer_kind == "control_tool":
            if _CONTROL_OWNER_NAMESPACE.fullmatch(producer_stage) is None:
                _integrity_error(
                    "Control-tool producer_stage must be a canonical owner namespace.",
                    contract=contract,
                    field=f"artifacts[{artifact_index}].producer_stage",
                    producer_stage=producer_stage,
                )
        elif producer_stage not in stage_ids:
            _integrity_error(
                "Workflow artifact producer_stage references an unknown stage.",
                contract=contract,
                field=f"artifacts[{artifact_index}].producer_stage",
                producer_stage=producer_stage,
                producer_kind=producer_kind,
            )
        for consumer_index, consumer_stage in enumerate(artifact["consumer_stages"]):
            if consumer_stage not in stage_ids:
                _integrity_error(
                    "Artifact consumer_stages references an unknown stage.",
                    contract=contract,
                    field=f"artifacts[{artifact_index}].consumer_stages[{consumer_index}]",
                    consumer_stage=consumer_stage,
                )


def _load_runtime_contract_universe(repo_workdir: str | Path) -> _RuntimeContractUniverse:
    repo = _resolve_repo_workdir(repo_workdir)
    stage_payload = _load_contract_yaml(repo, "stage_specs")
    artifact_payload = _load_contract_yaml(repo, "artifact_contracts")
    stages = _validate_stage_specs(stage_payload)
    artifacts, producer_kinds = _validate_artifact_contracts(artifact_payload)
    _validate_local_coherence(stages, artifacts, producer_kinds)
    return _RuntimeContractUniverse(stages=tuple(stages), artifacts=tuple(artifacts))


def validate_runtime_contract_payloads(
    stage_specs: dict[str, Any],
    artifact_contracts: dict[str, Any],
    policy_pack: dict[str, Any],
) -> ValidatedRuntimeContractPayloads:
    """Validate detached contract payloads without reading or writing files."""

    if type(stage_specs) is not dict or type(artifact_contracts) is not dict:
        raise RuntimeStateError("Runtime contract payloads must contain objects")
    if type(policy_pack) is not dict:
        raise RuntimeStateError("policy_packs/default.yaml must contain an object")
    stage_payload = deepcopy(stage_specs)
    artifact_payload = deepcopy(artifact_contracts)
    policy_payload = deepcopy(policy_pack)
    stages = _validate_stage_specs(stage_payload)
    artifacts, producer_kinds = _validate_artifact_contracts(artifact_payload)
    _validate_local_coherence(stages, artifacts, producer_kinds)
    if policy_payload.get("schema_version") != "multi-agent-brief-policy-pack/v1":
        raise RuntimeStateError("policy_packs/default.yaml schema_version is invalid")
    policy_identity = policy_payload.get("policy_pack")
    if type(policy_identity) is not dict or policy_identity.get("name") != "default":
        raise RuntimeStateError("policy_packs/default.yaml identity is invalid")
    return ValidatedRuntimeContractPayloads(
        stage_specs=stage_payload,
        artifact_contracts=artifact_payload,
        policy_pack=policy_payload,
        stages=tuple(deepcopy(stages)),
        artifacts=tuple(deepcopy(artifacts)),
    )


def load_runtime_contract_payloads(
    repo_workdir: str | Path,
) -> ValidatedRuntimeContractPayloads:
    """Load and validate the exact three-file runtime contract universe."""

    repo = _resolve_repo_workdir(repo_workdir)
    stage_payload = _load_contract_yaml(repo, "stage_specs")
    artifact_payload = _load_contract_yaml(repo, "artifact_contracts")
    policy_payload = _load_yaml(
        _contract_file(repo, CONTRACT_REFERENCES["default_policy_pack"])
    )
    if type(policy_payload) is not dict:
        raise RuntimeStateError("policy_packs/default.yaml must contain an object")
    return validate_runtime_contract_payloads(
        stage_payload,
        artifact_payload,
        policy_payload,
    )


def load_stage_specs(repo_workdir: str | Path) -> list[dict[str, Any]]:
    return list(_load_runtime_contract_universe(repo_workdir).stages)


def load_artifact_contracts(repo_workdir: str | Path) -> list[dict[str, Any]]:
    return list(_load_runtime_contract_universe(repo_workdir).artifacts)


def load_default_policy_pack(repo_workdir: str | Path) -> dict[str, Any]:
    repo = _resolve_repo_workdir(repo_workdir)
    data = _load_yaml(_contract_file(repo, CONTRACT_REFERENCES["default_policy_pack"]))
    if not isinstance(data, dict):
        raise RuntimeStateError("policy_packs/default.yaml must contain an object")
    return data


def _stage_ids(stages: list[dict[str, Any]]) -> list[str]:
    return [str(stage["stage_id"]) for stage in stages if stage.get("stage_id")]


def _artifact_ids(artifacts: list[dict[str, Any]]) -> set[str]:
    return {
        str(artifact["artifact_id"])
        for artifact in artifacts
        if artifact.get("artifact_id")
    }


def _artifact_map(artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(artifact["artifact_id"]): artifact
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
