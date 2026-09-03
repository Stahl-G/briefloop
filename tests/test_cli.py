"""Tests for CLI toolbox commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import build_parser, main
import multi_agent_brief.product.post_final_assessment as assessment_module
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
)
import multi_agent_brief.semantic_evaluator.runner as runner_module
from tests.test_post_final_assessment import (
    _finalized_local_workspace,
    _fixture_service,
    _policy_payload,
)


def complete_init_args(workspace, *, language="zh-CN", industry="finance", extra=None):
    args = [
        "init",
        str(workspace),
        "--language",
        language,
        "--company",
        "Test Company",
        "--industry",
        industry,
        "--title",
        "Weekly Brief",
        "--audience",
        "management",
        "--cadence",
        "weekly",
        "--source-profile",
        "research",
    ]
    parser = build_parser()
    subcommands = next(
        action.choices for action in parser._actions if getattr(action, "choices", None)
    )
    init_options = {
        option
        for action in subcommands["init"]._actions
        for option in action.option_strings
    }
    if "--task-objective" in init_options:
        # retain only the strict SQLite initialization contract.
        args.extend(["--task-objective", "Track material finance developments."])
    if extra:
        args.extend(extra)
    return args


def test_cli_init_creates_workspace(tmp_path, capsys):
    workspace = tmp_path / "ws"

    assert main(complete_init_args(workspace)) == 0
    output = capsys.readouterr().out
    assert (workspace / "config.yaml").exists()
    assert (workspace / "sources.yaml").exists()
    assert (workspace / "input").exists()
    input_readme = (workspace / "input" / "README.md").read_text(encoding="utf-8")
    context_readme = (workspace / "input" / "context" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "input/context" in input_readme
    assert "简报示例 Markdown" in input_readme
    assert "previous_weekly_reference.md" in context_readme
    assert "input/context" in output
    assert "简报示例 Markdown" in output
    assert "Claim Ledger" in output


def test_cli_init_rejects_nested_workspace_before_any_write(tmp_path, capsys):
    outer = tmp_path / "outer"
    assert main(complete_init_args(outer)) == 0
    capsys.readouterr()
    before = {
        path.relative_to(outer).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in outer.rglob("*")
        if path.is_file()
    }
    nested = outer / "nested"

    assert main(complete_init_args(nested, language="en-US")) == 1

    assert capsys.readouterr().out.strip() == "[error] workspace_target_nested"
    assert not nested.exists()
    after = {
        path.relative_to(outer).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in outer.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_quality_laj_cli_executes_assessment_next_request(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public commands emit a request accepted verbatim by assessment-run."""

    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls, terminal_mode="finding")
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    monkeypatch.setattr(
        assessment_module,
        "PostFinalAssessmentService",
        lambda _workspace: service,
    )
    policy_json = json.dumps(_policy_payload(), sort_keys=True)
    assert (
        main(
            [
                "quality",
                "laj",
                "policy-set",
                "--workspace",
                str(workspace),
                "--policy-json",
                policy_json,
                "--json",
            ]
        )
        == 0
    )
    policy_payload = json.loads(capsys.readouterr().out)
    policy_id = policy_payload["policy_revision_id"]

    next_args = [
        "quality",
        "laj",
        "assessment-next",
        "--workspace",
        str(workspace),
        "--policy-revision-id",
        policy_id,
        "--human-actor-id",
        "human-cli",
        "--human-request-id",
        "cli-assessment-next-1",
        "--assessment-purpose",
        "post_final_review",
        "--json",
    ]
    assert main(next_args) == 0
    first_next = json.loads(capsys.readouterr().out)
    assert first_next["ok"] is True
    assert first_next["status"] == "ready"
    request = first_next["request"]
    assert request["human_request_id"] == "cli-assessment-next-1"
    assert request["assessment_generation"] == 1

    assert main(next_args[:-1] + ["--json"]) == 0
    second_next = json.loads(capsys.readouterr().out)
    assert second_next == first_next

    invalid_args = list(next_args)
    invalid_args[invalid_args.index("--policy-revision-id") + 1] = "stale-policy"
    assert main(invalid_args) == 1
    invalid = json.loads(capsys.readouterr().out)
    assert invalid["ok"] is False
    assert invalid["reason_code"] == "post_final_assessment_policy_conflict"

    request_json = json.dumps(request, sort_keys=True)
    assert (
        main(
            [
                "quality",
                "laj",
                "assessment-run",
                "--workspace",
                str(workspace),
                "--request-json",
                request_json,
                "--json",
            ]
        )
        == 0
    )
    assessed = json.loads(capsys.readouterr().out)
    assert assessed["ok"] is True
    assert assessed["status"] == "available"
    list_args = [
        "quality",
        "laj",
        "assessment-list",
        "--workspace",
        str(workspace),
    ]
    assert main(list_args + ["--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    listed_result = listing["assessments"][0]
    assert listed_result["assessment_result_id"] == assessed["assessment_result_id"]
    assert (
        listed_result["assessment_result_fingerprint"]
        == assessed["assessment_result_fingerprint"]
    )
    for field in (
        "assessed_unit_count",
        "finding_count",
        "withheld_finding_count",
        "abstention_count",
        "reason_codes",
    ):
        assert field in listed_result

    assert main(list_args) == 0
    assert capsys.readouterr().out.splitlines() == [
        "[quality laj assessment-list] ok: True",
        (
            "- assessment_generation=1 assessment_purpose=post_final_review "
            "requested_model_id=public-compatible-model-v1 "
            "terminal_evidence_class=available"
        ),
        (
            f"  assessment_result_id={listed_result['assessment_result_id']} "
            "assessment_result_fingerprint="
            f"{listed_result['assessment_result_fingerprint']}"
        ),
        (
            f"  assessed_unit_count={listed_result['assessed_unit_count']} "
            f"finding_count={listed_result['finding_count']} "
            "withheld_finding_count="
            f"{listed_result['withheld_finding_count']} "
            f"abstention_count={listed_result['abstention_count']} "
            f"reason_codes={listed_result['reason_codes']}"
        ),
    ]
    calls_before_replay = len(calls)

    replay_service = _fixture_service(workspace, calls, terminal_mode="finding")
    replay_service._adapter_factory = lambda _execution: (_ for _ in ()).throw(
        AssertionError("public CLI replay touched provider")
    )
    monkeypatch.setattr(
        assessment_module,
        "PostFinalAssessmentService",
        lambda _workspace: replay_service,
    )
    assert (
        main(
            [
                "quality",
                "laj",
                "assessment-run",
                "--workspace",
                str(workspace),
                "--request-json",
                request_json,
                "--json",
            ]
        )
        == 0
    )
    replayed = json.loads(capsys.readouterr().out)
    assert replayed["ok"] is True
    assert replayed["replayed"] is True
    assert main(list_args + ["--json"]) == 0
    assert json.loads(capsys.readouterr().out) == listing
    assert len(calls) == calls_before_replay


def test_cli_init_can_configure_initial_news_backfill(tmp_path):
    workspace = tmp_path / "ws"

    rc = main(
        complete_init_args(
            workspace,
            language="en-US",
            industry="manufacturing",
            extra=[
                "--source-profile",
                "llm_decide",
                "--web-search-mode",
                "runtime_tool",
                "--initial-news-backfill",
                "--preferred-news-domains",
                "reuters.com, bloomberg.com",
                "--excluded-news-domains",
                "spam.example.com",
            ],
        )
    )

    assert rc == 0
    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["web_search"]["mode"] == "runtime_tool"
    assert "backend" not in sources["web_search"]
    backfill = sources["web_search"]["initial_news_backfill"]
    assert backfill["enabled"] is True
    assert backfill["days"] == 7
    assert backfill["daily_max_results"] == 20
    assert backfill["note"] == (
        "Planning preference only; Store-derived RuntimeHost actions govern"
        " any authorized acquisition."
    )
    customization = sources["source_discovery"]["search_customization"]
    assert "task_objective" in customization["derive_queries_from"]
    assert customization["daily_backfill_uses_user_need_terms"] is True
    source_selection = sources["source_discovery"]["news_source_selection"]
    assert source_selection["preferred_domains"] == ["reuters.com", "bloomberg.com"]
    assert source_selection["excluded_domains"] == ["spam.example.com"]
    assert source_selection["do_not_use_fixed_personal_domain_list"] is True
    domain_config = sources["web_search"]["news_source_domains"]
    assert domain_config["preferred_domains"] == ["reuters.com", "bloomberg.com"]
    assert domain_config["excluded_domains"] == ["spam.example.com"]
    assert sources["filing_resolver"]["note"] == (
        "Disabled by default. Configure filing_resolver tickers explicitly "
        "in sources.yaml."
    )


def test_cli_init_rejects_initial_news_backfill_without_llm_decide(tmp_path, capsys):
    workspace = tmp_path / "ws"

    rc = main(
        complete_init_args(
            workspace,
            language="en-US",
            industry="manufacturing",
            extra=[
                "--web-search-mode",
                "runtime_tool",
                "--initial-news-backfill",
            ],
        )
    )

    assert rc == 1
    assert (
        "--initial-news-backfill requires --source-profile llm_decide because "
        "it belongs to the llm_decide source-discovery profile."
        in capsys.readouterr().out
    )
    assert not (workspace / "sources.yaml").exists()


def test_pyproject_exposes_briefloop_shell_alias():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    entrypoint = '"multi_agent_brief.cli.main:main"'
    assert f"multi-agent-brief = {entrypoint}" in text
    assert f"briefloop = {entrypoint}" in text


def test_onboard_template_writes_json(tmp_path, capsys):
    """onboard --template writes a template onboarding.json."""
    out = tmp_path / "onboarding.json"
    exit_code = main(["onboard", "--template", "--output", str(out)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "company_or_org" in data
    assert "task_objective" in data
    assert "audience_plain" in data


def test_onboard_validate_accepts_valid_file(tmp_path, capsys):
    """onboard --validate accepts a complete onboarding.json."""
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps(
            {
                "company_or_org": "阿特斯",
                "industry_or_theme": "光伏",
                "task_objective": "行业简报",
            }
        ),
        encoding="utf-8",
    )
    exit_code = main(["onboard", "--validate", str(valid)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Required fields: OK" in captured.out


def test_onboard_validate_rejects_missing_fields(tmp_path, capsys):
    """onboard --validate returns 1 when required fields are missing."""
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(
        json.dumps(
            {
                "audience_plain": "management",
            }
        ),
        encoding="utf-8",
    )
    exit_code = main(["onboard", "--validate", str(incomplete)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Missing required fields" in captured.out


def test_onboard_validate_rejects_invalid_json(tmp_path, capsys):
    """onboard --validate returns 1 for invalid JSON."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    exit_code = main(["onboard", "--validate", str(bad)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Invalid JSON" in captured.out


def test_onboard_validate_rejects_missing_file(tmp_path, capsys):
    """onboard --validate returns 1 for nonexistent file."""
    exit_code = main(["onboard", "--validate", str(tmp_path / "nope.json")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not found" in captured.out
