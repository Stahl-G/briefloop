"""Submission semantics for the init web wizard (single bootstrap authority)."""

from __future__ import annotations

import json
import os

from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.policy import derived_id
from multi_agent_brief.product.init_web.submit import (
    SUBMISSION_SCHEMA,
    InitWebSubmitter,
    SubmissionError,
    _profile_from_payload,
)


def _body(request_id: str, target: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workspace_target": target,
        "selections": {
            "company": "ExampleCo",
            "report_type": "management_monthly",
            "industry_or_theme": "manufacturing",
            "task_objective": "Prepare the weekly manufacturing brief.",
            "brief_title": "ExampleCo weekly brief",
            "audience": "management",
            "interface_language": "zh",
            "output_language": "zh",
            "cadence": "weekly",
            "max_source_age_days": 7,
            "focus_areas": ["operations", "policy"],
            "output_formats": ["markdown", "docx"],
            "forbidden_sources": [],
            "web_search_mode": "disabled",
            "output_extent": "balanced",
        },
        "raw_free_text": "weekly manufacturing brief for management",
        "discarded": [],
        "human_confirmation": True,
    }
    payload.update(overrides)
    return {
        "schema_version": SUBMISSION_SCHEMA,
        "request_id": request_id,
        "payload": payload,
    }


def _revision(workspace: Path) -> int:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        return store.load_snapshot(head.current_run_id).store_revision


def _submit_ok(
    submitter: InitWebSubmitter, body: dict[str, object]
) -> dict[str, object]:
    status, response = submitter.submit(body)
    assert status == 200
    assert response["ok"] is True
    return response


def _public_web_body(
    request_id: str,
    target: str,
    *,
    session_id: str = "web-session",
    search_domains: list[str] | None = None,
) -> dict[str, object]:
    body = _body(request_id, target)
    payload = body["payload"]
    assert isinstance(payload, dict)
    selections = payload["selections"]
    assert isinstance(selections, dict)
    selections.update(
        {
            "source_profile": "llm_decide",
            "web_search_mode": "external_api",
            "search_backend": "tavily",
            "search_domains": [] if search_domains is None else search_domains,
        }
    )
    payload.update(
        {
            "completion_target": "finalized_local",
            "repair_budget": 1,
            "search_secret_session_id": session_id,
        }
    )
    return body


def test_committed_submission_creates_runnable_workspace_and_real_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0001", "web-ws")
    response = _submit_ok(submitter, body)

    assert response["status"] == "committed"
    workspace = tmp_path / "web-ws"
    assert (workspace / "config.yaml").is_file()
    config = yaml.safe_load((workspace / "config.yaml").read_text(encoding="utf-8"))
    assert config["output"]["html_report"]["auto_open"] is False
    assert (workspace / ".codex" / "config.toml").is_file()
    assert (workspace / "briefloop.db").is_file()
    expected_receipt_id = derived_id(
        "REQ-CX-INIT", response["workspace_id"], response["run_id"]
    )
    assert response["transaction_id"] == expected_receipt_id
    assert response["committed_revision"] >= 1
    assert response["execution_authorized"] is False
    assert response["completion_target"] is None
    assert response["repair_budget"] is None
    receipt = response["receipt"]
    assert receipt["transaction_id"] == expected_receipt_id
    assert receipt["run_id"] == response["run_id"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        stored = store.load_transaction_receipt(response["run_id"], expected_receipt_id)
    assert stored is not None
    assert stored.transaction_id == response["transaction_id"]
    revision_before = _revision(workspace)
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["run_id"] == response["run_id"]
    assert _revision(workspace) == revision_before


def test_public_web_submission_stores_tavily_key_outside_run_contract(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _public_web_body("REQ-WEB00001", "web-search-ws")
    payload = body["payload"]
    assert isinstance(payload, dict)
    selections = payload["selections"]
    assert isinstance(selections, dict)
    selections.pop("search_domains")
    configured = submitter.configure_search_secret(
        session_id="web-session",
        body={"provider": "tavily", "api_key": "tvly-test-secret-123"},
    )
    assert configured == {
        "ok": True,
        "provider": "tavily",
        "api_key_env": "TAVILY_API_KEY",
        "configured": True,
    }

    response = _submit_ok(submitter, body)

    workspace = tmp_path / "web-search-ws"
    secret_path = workspace / ".env"
    assert secret_path.read_text(encoding="utf-8") == (
        "TAVILY_API_KEY=tvly-test-secret-123\n"
    )
    # POSIX mode bits express the workspace-secret contract. Windows reports
    # synthetic mode bits and protects the file through its inherited ACL.
    if os.name != "nt":
        assert secret_path.stat().st_mode & 0o777 == 0o600
    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["source_strategy"]["profile"] == "llm_decide"
    assert sources["web_search"]["mode"] == "external_api"
    assert sources["web_search"]["backend"] == "tavily"
    assert sources["web_search"]["api_key_env"] == "TAVILY_API_KEY"
    assert (
        sources["source_discovery"]["news_source_selection"]["preferred_domains"] == []
    )
    assert sources["web_search"]["news_source_domains"]["preferred_domains"] == []
    assert response["execution_authorized"] is False
    assert response["source_discovery_authorized"] is True
    assert response["completion_target"] == "finalized_local"
    assert response["repair_budget"] == 1
    assert response["search_secret_status"] == "ready"
    assert response["source_discovery"] == {
        "mode": "pre_provider_authorization",
        "profile": "llm_decide",
        "backend": "tavily",
        "api_key_env": "TAVILY_API_KEY",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_execution_authorizations) == 0
    assert len(snapshot.run_source_discovery_authorizations) == 1
    assert snapshot.run_source_discovery_authorizations[0].route_id == "web-search"
    assert "tvly-test-secret-123" not in json.dumps(response)
    config_text = (workspace / "config.yaml").read_text(encoding="utf-8")
    assert "tvly-test-secret-123" not in config_text
    assert b"tvly-test-secret-123" not in (workspace / "briefloop.db").read_bytes()


def test_public_web_submission_freezes_canonical_search_domains_and_replays(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _public_web_body(
        "REQ-DOMAINS-001",
        "domain-bound",
        search_domains=[" OpenAI.COM ", "docs.openai.com"],
    )
    submitter.configure_search_secret(
        session_id="web-session",
        body={"provider": "tavily", "api_key": "tvly-domain-secret"},
    )

    committed = _submit_ok(submitter, body)
    workspace = tmp_path / "domain-bound"
    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["source_discovery"]["news_source_selection"][
        "preferred_domains"
    ] == ["docs.openai.com", "openai.com"]
    assert sources["web_search"]["news_source_domains"]["preferred_domains"] == [
        "docs.openai.com",
        "openai.com",
    ]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert snapshot.store_revision == committed["committed_revision"]
    db_bytes = (workspace / "briefloop.db").read_bytes()

    replayed = _submit_ok(
        submitter,
        _public_web_body(
            "REQ-DOMAINS-001",
            "domain-bound",
            search_domains=["docs.openai.com", "openai.com"],
        ),
    )
    assert replayed["status"] == "replayed"
    assert (workspace / "briefloop.db").read_bytes() == db_bytes


@pytest.mark.parametrize("max_source_age_days", [7, 30])
def test_public_web_submission_freezes_confirmed_report_window(
    tmp_path: Path,
    max_source_age_days: int,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    target = f"web-search-{max_source_age_days}"
    body = _public_web_body(
        f"REQ-WINDOW-{max_source_age_days}",
        target,
    )
    payload = body["payload"]
    assert isinstance(payload, dict)
    selections = payload["selections"]
    assert isinstance(selections, dict)
    selections["max_source_age_days"] = max_source_age_days
    submitter.configure_search_secret(
        session_id="web-session",
        body={"provider": "tavily", "api_key": "tvily-window-secret"},
    )

    _submit_ok(submitter, body)

    workspace = tmp_path / target
    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["web_search"]["recency_days"] == 7
    assert sources["web_search"]["initial_news_backfill"]["recency_days"] == 30
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_contract_bindings) == 1
    assert (
        snapshot.run_contract_bindings[0].run_direction.max_source_age_days
        == max_source_age_days
    )


def test_web_workspace_matches_cli_init_authority_shape(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0002", "web-ws")
    response = _submit_ok(submitter, body)

    profile = _profile_from_payload(body["payload"])  # type: ignore[arg-type]
    cli_target = tmp_path / "cli-ws"
    create_workspace(cli_target, profile, force=False)

    def _bootstrap(path: Path) -> dict[str, object]:
        config = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
        bootstrap = config["controlstore_v2"]
        bootstrap["workspace_id"] = "<id>"
        bootstrap["run_id"] = "<id>"
        return bootstrap

    assert _bootstrap(tmp_path / "web-ws") == _bootstrap(cli_target)


def test_identical_resubmit_is_replayed_with_zero_writes(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0003", "web-ws")
    first = _submit_ok(submitter, body)
    workspace = tmp_path / "web-ws"
    revision_before = _revision(workspace)

    restarted = InitWebSubmitter(base_dir=tmp_path)
    status, second = restarted.submit(body)
    assert status == 200
    assert second["status"] == "replayed"
    assert second["workspace_id"] == first["workspace_id"]
    assert second["run_id"] == first["run_id"]
    assert second["transaction_id"] == first["transaction_id"]
    assert second["committed_revision"] == first["committed_revision"]
    assert second["receipt"] == first["receipt"]
    assert _revision(workspace) == revision_before


def test_human_confirmation_is_required(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0005", "web-ws", human_confirmation=False)
    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(body)
    assert exc_info.value.error_code == "human_confirmation_required"
    assert exc_info.value.http_status == 422
    assert not (tmp_path / "web-ws").exists()
