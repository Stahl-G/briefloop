"""Tests for CLI toolbox commands."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile

import pytest
import yaml

from multi_agent_brief.cli.main import build_parser, main
from multi_agent_brief.cli import product_commands
import multi_agent_brief.product.post_final_assessment as assessment_module
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
)
import multi_agent_brief.semantic_evaluator.runner as runner_module
from tests.helpers import initialize_workspace
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


def test_cli_init_rejects_symlink_alias_into_existing_workspace_before_writes(
    tmp_path,
    capsys,
):
    outer = tmp_path / "outer"
    assert main(complete_init_args(outer)) == 0
    capsys.readouterr()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(outer / "input" / "context", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    before = {
        path.relative_to(outer).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in outer.rglob("*")
        if path.is_file()
    }

    assert main(complete_init_args(alias / "nested", language="en-US")) == 1

    assert capsys.readouterr().out.strip() == "[error] workspace_target_nested"
    assert not (outer / "input" / "context" / "nested").exists()
    after = {
        path.relative_to(outer).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in outer.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_cli_init_rejects_lexically_nested_alias_that_points_outward(
    tmp_path,
    capsys,
):
    outer = tmp_path / "outer"
    assert main(complete_init_args(outer)) == 0
    capsys.readouterr()
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = outer / "input" / "context" / "outward"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")

    assert main(complete_init_args(alias / "nested", language="en-US")) == 1

    assert capsys.readouterr().out.strip() == "[error] workspace_target_nested"
    assert not (outside / "nested").exists()


def test_cli_init_uses_canonical_non_nested_alias_target(tmp_path, capsys):
    canonical_parent = tmp_path / "canonical"
    canonical_parent.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(canonical_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")

    assert main(complete_init_args(alias / "workspace")) == 0

    canonical_target = canonical_parent / "workspace"
    output = capsys.readouterr().out
    assert (canonical_target / "config.yaml").exists()
    assert str(canonical_target) in output
    assert str(alias / "workspace") not in output


@pytest.mark.macos_publication
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS /tmp alias row")
def test_cli_init_allows_macos_tmp_alias_for_non_nested_target(capsys):
    routed_parent = Path(tempfile.mkdtemp(prefix="briefloop-m5-", dir="/tmp"))
    canonical_parent = routed_parent.resolve()
    try:
        assert main(complete_init_args(routed_parent / "workspace")) == 0
        output = capsys.readouterr().out
        assert (canonical_parent / "workspace" / "config.yaml").exists()
        assert str(canonical_parent / "workspace") in output
    finally:
        shutil.rmtree(canonical_parent, ignore_errors=True)


def test_quality_html_help_states_the_truthful_four_tab_boundary(capsys):
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["quality", "html", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    normalized = " ".join(output.split())
    assert "local, static, read-only five-tab view" in normalized
    assert "local-finalized Brief" in normalized
    assert "deterministic Quality" in normalized
    assert "optional advisory LAJ (NOT MEASURED)" in normalized
    assert "Store- native Human guidance state" in normalized
    assert "reuse requires an explicit successor-start opt-in" in normalized
    assert "three-page" not in normalized


def test_quality_laj_help_exposes_review_and_explicit_successor_reuse(capsys):
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["quality", "laj", "--help"])

    assert exc.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    for command in (
        "policy-set",
        "assess",
        "status",
        "retry",
        "assessment-run",
        "assessment-next",
        "assessment-list",
        "review-open",
        "disposition",
        "draft",
        "approve",
        "review-status",
    ):
        assert command in output
    assert "store-upgrade" not in output
    assert "snapshot" not in output
    assert "reuse" in output
    assert "successor-start" in output
    assert "opt-in" in output


def test_review_open_is_an_ordinary_headless_path_without_internal_ids(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import multi_agent_brief.product.review_session as review_session

    observed: dict[str, object] = {}

    class _Server:
        def wait(self) -> None:
            observed["waited"] = True

        def close(self) -> None:
            observed["closed"] = True

    class _Launch:
        server = _Server()
        reason_code = "review_session_headless"
        user_status = "not_assessed"
        compatible_result_count = 0
        run_action_available = True
        next_run_consumption = "explicit_opt_in_successor_only"
        url = "http://127.0.0.1:43123/index.html#ephemeral"

    def _launch(
        workspace,
        assessment_result_id=None,
        assessment_result_fingerprint=None,
        *,
        open_browser=True,
    ):
        observed.update(
            {
                "workspace": workspace,
                "assessment_result_id": assessment_result_id,
                "assessment_result_fingerprint": assessment_result_fingerprint,
                "open_browser": open_browser,
            }
        )
        return _Launch()

    monkeypatch.setattr(review_session, "launch_actionable_review_session", _launch)
    workspace = initialize_workspace(tmp_path / "fresh-current-schema")

    assert (
        main(
            [
                "quality",
                "laj",
                "review-open",
                "--workspace",
                str(workspace),
                "--no-browser",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["user_status"] == "not_assessed"
    assert payload["compatible_result_count"] == 0
    assert payload["run_action_available"] is True
    assert payload["selection_required"] is False
    assert payload["runtime_authority"] is False
    assert payload["next_run_consumption"] == "explicit_opt_in_successor_only"
    assert observed == {
        "workspace": workspace.resolve(),
        "assessment_result_id": None,
        "assessment_result_fingerprint": None,
        "open_browser": False,
        "waited": True,
    }


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


def test_readmes_bind_v0151_work_to_v0153_and_keep_v014_historical_boundary() -> None:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)

    root = Path(__file__).resolve().parents[1]
    english = (root / "README.md").read_text(encoding="utf-8")
    chinese = (root / "README.zh-CN.md").read_text(encoding="utf-8")
    readme_en = (root / "README_en.md").read_text(encoding="utf-8")

    require(
        "The v0.15.1 engineering target, included in v0.15.3" in english
        and "introduced\nStore-qualified post-final review" in english,
        "English README does not bind v0.15.1 AI Second Opinion work to v0.15.3",
    )
    historical_english = english.split(
        "Prior v0.14.0 completed the SQLite-only cutover",
        1,
    )[1].split("The carried-forward supported report tooling", 1)[0]
    require(
        "Store-qualified AI Second Opinion" not in historical_english,
        "English README attributes v0.15.1 AI Second Opinion to v0.14.0",
    )

    require(
        "v0.15.1 工程目标已包含在 v0.15.3 中" in chinese
        and "它引入了 Store-qualified post-final 审阅" in chinese,
        "Chinese README does not bind v0.15.1 AI Second Opinion work to v0.15.3",
    )
    historical_chinese = chinese.split(
        "此前的 v0.14.0 完成 SQLite-only 切换",
        1,
    )[1].split("延续的受支持报告工具", 1)[0]
    require(
        "AI 第二意见" not in historical_chinese,
        "Chinese README attributes v0.15.1 AI Second Opinion to v0.14.0",
    )

    require(
        "Store-qualified post-final review" not in readme_en,
        "README_en contains the same false release attribution",
    )


def test_public_docs_require_fresh_workspace_instead_of_store_upgrade() -> None:
    root = Path(__file__).resolve().parents[1]
    public_docs = {
        path: (root / path).read_text(encoding="utf-8")
        for path in (
            "README.md",
            "README.zh-CN.md",
            "docs/MIGRATION.md",
            "docs/architecture-status.md",
            "docs/support-matrix.md",
        )
    }

    assert all("store-upgrade" not in text for text in public_docs.values())
    assert "Old development workspaces are unsupported" in public_docs["README.md"]
    assert "旧 development workspace 不受支持" in public_docs["README.zh-CN.md"]
    migration = public_docs["docs/MIGRATION.md"]
    assert "fresh current-schema workspaces" in migration
    assert "Older development" in migration
    assert "workspaces are unsupported" in migration


def test_quality_html_reports_unsupported_publication_without_effects(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    store_before = (workspace / "briefloop.db").read_bytes()
    output_before = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in (workspace / "output").rglob("*")
        if path.is_file()
    }
    names_before = sorted(
        path.relative_to(workspace).as_posix()
        for path in (workspace / "output").rglob("*")
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.render."
        "supports_retained_directory_publication",
        lambda: False,
    )

    assert (
        main(
            [
                "quality",
                "html",
                "--workspace",
                str(workspace),
                "--json",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out

    assert "[quality html] static export:" not in output
    assert json.loads(output) == {
        "ok": False,
        "error": "brief_html_publication_unsupported",
        "workspace": str(workspace),
        "boundary": "read_only_static_export",
    }
    assert (workspace / "briefloop.db").read_bytes() == store_before
    assert {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in (workspace / "output").rglob("*")
        if path.is_file()
    } == output_before
    assert (
        sorted(
            path.relative_to(workspace).as_posix()
            for path in (workspace / "output").rglob("*")
        )
        == names_before
    )


def test_packs_bundle_help_states_retired_internal_only_boundary(capsys):
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["packs", "bundle", "--help"])

    assert exc.value.code == 0
    normalized = " ".join(capsys.readouterr().out.split())
    assert "public command is retired and unavailable" in normalized
    assert "internal deterministic, capability-gated seam only" in normalized
    assert "Write a local bundle projection" not in normalized
    assert "safe local publication capability" not in normalized


@pytest.mark.parametrize(
    "argv",
    (
        ["sources", "--help"],
        ["sources", "decide", "--help"],
        ["sources", "materialize-pack", "--help"],
        ["sources", "add-file", "--help"],
        ["sources", "add-rss", "--help"],
        ["sources", "add-web-search", "--help"],
    ),
)
def test_retired_sources_help_is_truthful(capsys, argv):
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(argv)

    if exc.value.code != 0:
        raise AssertionError(f"{argv}: unexpected help exit {exc.value.code}")
    normalized = " ".join(capsys.readouterr().out.split())
    for required in (
        "Retired compatibility command",
        "unavailable",
        "runtime_command_unsupported",
        "init-web",
        "briefloop runtime continue --workspace <workspace>",
    ):
        if required not in normalized:
            raise AssertionError(f"{argv}: missing truthful help fragment {required!r}")
    for forbidden in (
        "Source discovery and management",
        "Resolve llm_decide profile into concrete source candidates",
        "Run web search to discover sources",
        "Merge approved source_candidates.yaml into sources.yaml",
        "Materialize explicit durable source records",
        "Copy local text evidence files",
        "Register an RSS/Atom feed",
        "Register a runtime web-search handoff task",
    ):
        if forbidden in normalized:
            raise AssertionError(f"{argv}: stale active help fragment {forbidden!r}")


@pytest.mark.parametrize(
    ("authority", "expected"),
    (
        ("fresh", "runtime_command_unsupported\n"),
        ("sqlite", "runtime_command_unsupported\n"),
        # Legacy JSON control files no longer change classification: the
        # workspace is treated as fresh and gets the same typed rejection.
        ("legacy", "runtime_command_unsupported\n"),
    ),
)
def test_packs_bundle_public_authority_guard_precedes_projection_without_effects(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
    expected: str,
) -> None:
    workspace = tmp_path / authority
    if authority == "sqlite":
        workspace.mkdir()
        (workspace / "briefloop.db").write_bytes(b"guard classification only")
    elif authority == "legacy":
        control = workspace / "output" / "intermediate" / "runtime_manifest.json"
        control.parent.mkdir(parents=True)
        control.write_text("{}\n", encoding="utf-8")

    before = (
        {
            path.relative_to(workspace).as_posix(): path.read_bytes()
            for path in workspace.rglob("*")
            if path.is_file()
        }
        if workspace.exists()
        else {}
    )

    def forbidden_projection(*args, **kwargs):
        del args, kwargs
        raise AssertionError("public guard must reject before bundle projection")

    monkeypatch.setattr(
        product_commands,
        "write_report_bundle_manifest",
        forbidden_projection,
    )

    assert (
        main(
            [
                "packs",
                "bundle",
                "--workspace",
                str(workspace),
                "--write-archives",
            ]
        )
        == 1
    )
    assert capsys.readouterr().out == expected
    after = (
        {
            path.relative_to(workspace).as_posix(): path.read_bytes()
            for path in workspace.rglob("*")
            if path.is_file()
        }
        if workspace.exists()
        else {}
    )
    assert after == before
    assert not (workspace / "output" / "report_bundle_manifest.json").exists()
    assert not (workspace / "output" / "delivery_bundle.zip").exists()
    assert not (workspace / "output" / "audit_bundle.zip").exists()


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


def test_cli_version(capsys):
    assert main(["version"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip()


def test_pyproject_exposes_briefloop_shell_alias():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    entrypoint = '"multi_agent_brief.cli.main:main"'
    assert f"multi-agent-brief = {entrypoint}" in text
    assert f"briefloop = {entrypoint}" in text


def test_cli_run_command_creates_handoff(capsys):
    """run --runtime operator is retired: typed rejection with zero workspace writes."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    config = d / "config.yaml"
    config.write_text(
        "project:\n  name: test\noutput:\n  path: output\n", encoding="utf-8"
    )
    (d / "user.md").write_text("# test\n", encoding="utf-8")
    before_files = {
        path.relative_to(d).as_posix(): path.read_bytes()
        for path in d.rglob("*")
        if path.is_file()
    }
    exit_code = main(
        ["run", "--runtime", "operator", "--config", str(config), "--skip-doctor"]
    )
    captured = capsys.readouterr()
    # retired public `run --runtime operator` handoff surface and
    # its output/intermediate control artifacts; the Codex SQLite ControlStore
    # runtime is the sole runtime authority.
    assert exit_code == 1
    assert captured.out == "[run] runtime_adapter_unsupported\n"
    assert "/generate-brief" not in captured.out
    after_files = {
        path.relative_to(d).as_posix(): path.read_bytes()
        for path in d.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
    assert not (d / "output").exists()


def test_cli_prepare_is_deprecated_and_does_not_generate_outputs(
    tmp_path: Path, capsys
):
    """prepare is retired: typed rejection and it must not generate any outputs."""
    ws = tmp_path / "ws"
    assert main(complete_init_args(ws, extra=["--source-profile", "conservative"])) == 0
    capsys.readouterr()
    before_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }

    result = main(["prepare", "--config", str(ws / "config.yaml")])
    captured = capsys.readouterr()

    # retired public `prepare` surface and its deprecation
    # message; the workspace authority guard rejects it before dispatch.
    assert result == 1
    assert captured.out == "runtime_command_unsupported\n"
    assert "/generate-brief" not in captured.out
    after_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
    assert not (ws / "output" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "intermediate" / "candidate_claims.json").exists()
    assert not (ws / "output" / "intermediate" / "screened_candidates.json").exists()
    assert not (ws / "output" / "intermediate" / "audited_brief.md").exists()
    assert not (ws / "output" / "intermediate" / "audit_report.json").exists()


def test_core_brief_pipeline_is_removed():
    assert not Path("src/multi_agent_brief/core/pipeline.py").exists()


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
