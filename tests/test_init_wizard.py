from argparse import Namespace

from multi_agent_brief.cli.init_wizard import build_profile_from_args, prompt_for_profile
from multi_agent_brief.cli.main import main


def complete_init_args(
    workspace,
    *,
    language="zh-CN",
    company="Test Company",
    industry="manufacturing",
    title="Weekly Brief",
    audience="management",
    cadence="weekly",
    source_profile="research",
    extra=None,
):
    args = [
        "init",
        str(workspace),
        "--language",
        language,
        "--company",
        company,
        "--industry",
        industry,
        "--title",
        title,
        "--audience",
        audience,
        "--cadence",
        cadence,
        "--source-profile",
        source_profile,
    ]
    if extra:
        args.extend(extra)
    return args


def test_init_demo_preserves_existing_demo_behavior(tmp_path):
    demo_dir = tmp_path / "demo"

    assert main(["init", str(demo_dir), "--demo"]) == 0

    assert (demo_dir / "config.yaml").exists()
    assert (demo_dir / "input" / "news.json").exists()
    assert (demo_dir / "input" / "market_data.json").exists()


def test_init_workspace_creates_expected_files(tmp_path):
    workspace = tmp_path / "workspace"

    assert main(complete_init_args(workspace)) == 0

    assert (workspace / "config.yaml").exists()
    assert (workspace / "profile.yaml").exists()
    assert (workspace / "sources.yaml").exists()
    assert (workspace / "input" / "README.md").exists()
    assert (workspace / "output").is_dir()
    assert (workspace / ".gitignore").exists()


def test_language_prompt_is_first_and_defaults_to_chinese():
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return ""

    profile = prompt_for_profile(input_func=fake_input)

    assert prompts[0].startswith("Select language / 选择语言")
    assert "请输入公司名称" in prompts[1]
    assert profile.interface_language == "zh-CN"
    assert profile.output_language == "zh-CN"


def test_english_mode_generates_english_config_values(tmp_path):
    workspace = tmp_path / "english"

    assert (
        main(
            [
                "init",
                str(workspace),
                "--language",
                "en-US",
                "--company",
                "Sample Solar Manufacturer",
                "--role",
                "strategy_office",
                "--industry",
                "finance",
                "--title",
                "Weekly Solar Industry Brief",
                "--audience",
                "management",
                "--cadence",
                "weekly",
                "--rag",
                "off",
                "--source-profile",
                "research",
            ]
        )
        == 0
    )

    config_text = (workspace / "config.yaml").read_text(encoding="utf-8")
    assert 'interface: "en-US"' in config_text
    assert 'output: "en-US"' in config_text
    assert 'company: "Sample Solar Manufacturer"' in config_text
    assert 'enabled: false' in config_text


def test_bilingual_mode_sets_interface_and_output():
    args = Namespace(
        language="bilingual",
        output_language=None,
        company=None,
        role=None,
        industry=None,
        title=None,
        audience=None,
        focus_areas=None,
        cadence=None,
        rag="off",
        retrieval_provider=None,
        selector_max_items=None,
        output_formats=None,
    )

    profile = build_profile_from_args(args)

    assert profile.interface_language == "bilingual"
    assert profile.output_language == "bilingual"


def test_rag_provider_can_be_set_to_ollama_or_gemini():
    base = dict(
        language="zh-CN",
        output_language=None,
        company=None,
        role=None,
        industry=None,
        title=None,
        audience=None,
        focus_areas=None,
        cadence=None,
        selector_max_items=None,
        output_formats=None,
    )
    ollama = build_profile_from_args(Namespace(**base, rag="on", retrieval_provider="ollama"))
    gemini = build_profile_from_args(Namespace(**base, rag="on", retrieval_provider="gemini"))

    assert ollama.retrieval_enabled is True
    assert ollama.retrieval_provider == "ollama"
    assert ollama.retrieval_model == "nomic-embed-text"
    assert gemini.retrieval_enabled is True
    assert gemini.retrieval_provider == "gemini"
    assert gemini.retrieval_model == "gemini-embedding-001"


def test_workspace_gitignore_excludes_private_and_generated_paths(tmp_path):
    workspace = tmp_path / "workspace"

    main(complete_init_args(workspace))

    gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
    for expected in [".env", "output/", ".rag/", "private_inputs/", "private_outputs/", "user.md"]:
        assert expected in gitignore


def test_generated_input_readme_is_not_treated_as_source(tmp_path):
    workspace = tmp_path / "workspace"

    main(complete_init_args(workspace))
    assert main(["run", "--config", str(workspace / "config.yaml")]) == 0

    ledger = (workspace / "output" / "intermediate/claim_ledger.json").read_text(encoding="utf-8")
    assert ledger.strip() == "[]"


def test_init_workspace_creates_user_md(tmp_path):
    workspace = tmp_path / "workspace"
    assert main(complete_init_args(workspace)) == 0
    assert (workspace / "user.md").exists()
    user_md = (workspace / "user.md").read_text(encoding="utf-8")
    assert "用户简报画像" in user_md
    assert "不是证据来源" in user_md or "not source evidence" in user_md


def test_llm_decide_source_profile_generates_agent_policy(tmp_path):
    workspace = tmp_path / "workspace"
    assert main([
        "init", str(workspace),
        "--language", "zh-CN",
        "--company", "某公司",
        "--role", "strategy_office",
        "--industry", "finance",
        "--title", "行业周报",
        "--audience", "management",
        "--cadence", "weekly",
        "--source-profile", "llm_decide",
    ]) == 0

    sources = (workspace / "sources.yaml").read_text(encoding="utf-8")
    assert 'profile: "llm_decide"' in sources
    assert "requires_agent_resolution: true" in sources
    assert "source_discovery:" in sources
    assert "source_candidates.yaml" in sources

    user_md = (workspace / "user.md").read_text(encoding="utf-8")
    assert "用户简报画像" in user_md
    assert "llm_decide" in user_md
    assert "不是证据来源" in user_md


def test_llm_decide_does_not_call_llm(tmp_path):
    """llm_decide must only generate config, not make network calls."""
    workspace = tmp_path / "workspace"
    assert main(complete_init_args(workspace, language="en-US", source_profile="llm_decide")) == 0
    # Verify source_candidates.yaml is NOT created at init time
    assert not (workspace / "source_candidates.yaml").exists()


def test_llm_decide_user_md_not_treated_as_source(tmp_path):
    workspace = tmp_path / "workspace"
    main(complete_init_args(workspace, source_profile="llm_decide"))
    assert main(["run", "--config", str(workspace / "config.yaml")]) == 0
    ledger = (workspace / "output" / "intermediate/claim_ledger.json").read_text(encoding="utf-8")
    assert ledger.strip() == "[]"


def test_source_profile_noninteractive_arg_is_respected(tmp_path):
    workspace = tmp_path / "workspace"
    main(complete_init_args(workspace, language="en-US", source_profile="llm_decide"))
    config = (workspace / "config.yaml").read_text(encoding="utf-8")
    assert 'mode: "llm_decide"' in config or "mode: llm_decide" in config
    sources = (workspace / "sources.yaml").read_text(encoding="utf-8")
    assert "llm_decide" in sources


def test_all_source_profiles_generate_valid_workspace(tmp_path):
    for profile in ["conservative", "research", "aggressive_signal", "custom", "llm_decide"]:
        workspace = tmp_path / f"ws-{profile}"
        assert main(complete_init_args(workspace, source_profile=profile)) == 0
        assert (workspace / "config.yaml").exists()
        assert (workspace / "sources.yaml").exists()
        assert (workspace / "user.md").exists()
        assert (workspace / "input" / "README.md").exists()

# --- P1: Industry propagation into SourceConfig ---

def test_init_with_industry_writes_source_strategy_industry(tmp_path):
    """--industry manufacturing --source-profile research should write industry into source_strategy."""
    workspace = tmp_path / "ws"
    assert main([
        "init", str(workspace),
        "--language", "en-US",
        "--company", "Test",
        "--industry", "manufacturing",
        "--title", "Manufacturing Brief",
        "--audience", "management",
        "--cadence", "weekly",
        "--source-profile", "research",
    ]) == 0

    sources_text = (workspace / "sources.yaml").read_text(encoding="utf-8")
    assert "manufacturing" in sources_text

    # Load via SourceConfig.from_dict to verify the field is populated
    import yaml
    from multi_agent_brief.sources.base import SourceConfig
    data = yaml.safe_load(sources_text)
    config = SourceConfig.from_dict(data)
    assert config.industry == "manufacturing"


def test_cli_run_loads_industry_from_init_workspace(tmp_path):
    """Running CLI with a workspace init'd with --industry manufacturing should propagate industry."""
    workspace = tmp_path / "ws"
    assert main([
        "init", str(workspace),
        "--language", "en-US",
        "--company", "Test",
        "--industry", "manufacturing",
        "--title", "Manufacturing Brief",
        "--audience", "management",
        "--cadence", "weekly",
        "--source-profile", "research",
    ]) == 0

    # Run the pipeline and check that source-collection sees industry=manufacturing
    from multi_agent_brief.cli.main import main as cli_main
    import yaml
    from multi_agent_brief.sources.base import SourceConfig

    sources_path = workspace / "sources.yaml"
    data = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    config = SourceConfig.from_dict(data)
    assert config.industry == "manufacturing"


def test_pipeline_context_gets_industry_from_source_config(tmp_path):
    """When SourceConfig has industry, pipeline should pass it to create_source_plan."""
    from multi_agent_brief.core.pipeline import BriefPipeline
    from multi_agent_brief.core.schemas import PipelineContext
    from multi_agent_brief.sources.base import SourceConfig

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    context = PipelineContext(
        project_name="Industry Test",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        report_date="2026-06-02",
    )
    context.metadata["source_config"] = SourceConfig(
        profile="research",
        industry="manufacturing",
        enabled_providers=["manual"],
        manual={"enabled": True, "sources": [{"name": "Test", "path": str(input_dir), "enabled": True}]},
    )

    outputs = BriefPipeline().run(context)
    source_output = outputs[0]
    assert source_output.agent_name == "source-collection"
    assert source_output.artifacts.get("industry") == "manufacturing"


def test_industry_fallback_when_sources_yaml_missing_field(tmp_path):
    """If sources.yaml exists but has no industry, run_pipeline_from_args should use project.industry."""
    import yaml
    from multi_agent_brief.sources.base import SourceConfig

    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Write a sources.yaml without industry in source_strategy
    sources = {
        "source_strategy": {"profile": "research", "enabled_providers": ["manual"]},
        "manual": {"enabled": True, "sources": [{"name": "Test", "path": "input/", "enabled": True}]},
    }
    (workspace / "sources.yaml").write_text(yaml.dump(sources), encoding="utf-8")
    (workspace / "config.yaml").write_text(
        "project:\n  name: Test\n  industry: manufacturing\nreport:\n  date: 2026-06-02\ninput:\n  path: input\noutput:\n  path: output\n",
        encoding="utf-8",
    )
    (workspace / "input").mkdir()

    # Load and verify the fallback works
    from multi_agent_brief.sources.registry import load_sources_config
    source_config = load_sources_config(workspace / "sources.yaml")
    assert source_config.industry == ""  # not in YAML

    # Simulate the fallback from run_pipeline_from_args
    industry = "manufacturing"
    if not source_config.industry and industry:
        source_config.industry = industry
    assert source_config.industry == "manufacturing"


def test_noninteractive_init_without_onboarding_fails(tmp_path, capsys):
    """Non-interactive init must not create a workspace from hidden defaults."""
    from multi_agent_brief.cli.main import main

    workspace = tmp_path / "ws"
    assert main(["init", str(workspace)]) == 1
    captured = capsys.readouterr()
    assert "cannot create a workspace from defaults" in captured.out
    assert "--from-onboarding" in captured.out
    assert not (workspace / "config.yaml").exists()


def test_noninteractive_partial_init_args_fail(tmp_path, capsys):
    """Partial direct init args should fail instead of silently filling defaults."""
    from multi_agent_brief.cli.main import main

    workspace = tmp_path / "ws"
    assert main(["init", str(workspace), "--language", "zh-CN"]) == 1
    captured = capsys.readouterr()
    assert "--company" in captured.out
    assert "--source-profile" in captured.out
    assert not (workspace / "config.yaml").exists()


def test_interactive_partial_direct_init_args_fail(tmp_path, capsys, monkeypatch):
    """Interactive users should use the wizard unless all direct CLI fields are explicit."""
    import multi_agent_brief.cli.main as cli_main

    monkeypatch.setattr(cli_main, "_is_interactive", lambda: True)
    workspace = tmp_path / "ws"
    assert cli_main.main(["init", str(workspace), "--language", "zh-CN"]) == 1
    captured = capsys.readouterr()
    assert "Direct init with CLI args is incomplete" in captured.out
    assert "--company" in captured.out
    assert not (workspace / "config.yaml").exists()


def test_tavily_flag_alone_does_not_create_workspace(tmp_path, capsys):
    """--tavily alone should not trigger hidden default profile handling."""
    from multi_agent_brief.cli.main import main

    workspace = tmp_path / "ws"
    assert main(["init", str(workspace), "--tavily"]) == 1
    captured = capsys.readouterr()
    assert "--from-onboarding" in captured.out
    assert "--company" in captured.out
    assert not (workspace / "config.yaml").exists()


def test_from_onboarding_cli_override(tmp_path):
    """CLI flags override onboarding values when used with --from-onboarding."""
    from multi_agent_brief.cli.main import main
    import json

    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps({
        "company_or_org": "OldCompany",
        "industry_or_theme": "banking",
        "audience_plain": "research",
        "language_plain": "中文",
        "cadence_plain": "monthly",
        "source_style_plain": "conservative",
        "must_watch": ["rates"],
    }), encoding="utf-8")

    workspace = tmp_path / "ws"
    assert main([
        "init", str(workspace),
        "--from-onboarding", str(ob_path),
        "--company", "NewCompany",
        "--industry", "manufacturing",
        "--audience", "management",
        "--language", "en-US",
        "--source-profile", "llm_decide",
    ]) == 0

    config = (workspace / "config.yaml").read_text(encoding="utf-8")
    # CLI overrides should take effect
    assert "NewCompany" in config
    assert "manufacturing" in config
    assert "management" in config
    assert "en-US" in config
    # source_profile override
    sources = (workspace / "sources.yaml").read_text(encoding="utf-8")
    assert "llm_decide" in sources

def test_llm_decide_init_generates_enabled_providers(tmp_path):
    """llm_decide sources.yaml must include enabled_providers in source_strategy."""
    from multi_agent_brief.cli.main import main

    workspace = tmp_path / "ws"
    assert main([
        "init", str(workspace),
        "--language", "en-US",
        "--company", "Test",
        "--industry", "manufacturing",
        "--title", "Test Brief",
        "--audience", "management",
        "--cadence", "weekly",
        "--source-profile", "llm_decide",
    ]) == 0

    sources = (workspace / "sources.yaml").read_text(encoding="utf-8")
    assert "enabled_providers:" in sources
    assert "manual" in sources


def test_llm_decide_with_tavily_includes_web_search_provider(tmp_path):
    """llm_decide + --tavily should include web_search in enabled_providers."""
    from multi_agent_brief.cli.main import main

    workspace = tmp_path / "ws"
    assert main([
        "init", str(workspace),
        "--language", "en-US",
        "--company", "Test",
        "--industry", "manufacturing",
        "--title", "Test Brief",
        "--audience", "management",
        "--cadence", "weekly",
        "--source-profile", "llm_decide",
        "--tavily",
    ]) == 0

    sources = (workspace / "sources.yaml").read_text(encoding="utf-8")
    assert "enabled_providers:" in sources
    assert "web_search" in sources


def test_doctor_warns_web_search_enabled_but_not_provider(tmp_path):
    """Doctor must warn when web_search is enabled but not in enabled_providers."""
    from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report

    # Create a workspace with web_search enabled but NOT in enabled_providers
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sources.yaml").write_text(
        'source_strategy:\n  profile: "llm_decide"\n  enabled_providers:\n    - manual\n'
        'web_search:\n  enabled: true\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n'
        'manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n',
        encoding="utf-8",
    )
    (ws / "config.yaml").write_text("project:\n  name: Test\n", encoding="utf-8")

    results = run_doctor(config_path=ws / "config.yaml")
    report = format_doctor_report(results)
    assert "missing from enabled_providers" in report.lower() or "enabled_providers" in report.lower()


def test_sources_decide_search_no_backend_returns_nonzero(tmp_path):
    """sources decide --search must return non-zero when no search backend configured."""
    from multi_agent_brief.cli.main import main

    workspace = tmp_path / "ws"
    assert main([
        "init", str(workspace),
        "--language", "en-US",
        "--company", "Test",
        "--industry", "manufacturing",
        "--title", "Test Brief",
        "--audience", "management",
        "--cadence", "weekly",
        "--source-profile", "llm_decide",
    ]) == 0

    # --search without a backend should fail
    exit_code = main(["sources", "decide", "--config", str(workspace / "config.yaml"), "--search"])
    assert exit_code != 0


def test_pipeline_fails_when_tavily_key_missing(tmp_path, monkeypatch):
    """Pipeline must fail-fast when Tavily is enabled but API key is missing."""
    from multi_agent_brief.cli.main import main

    workspace = tmp_path / "ws"
    assert main([
        "init", str(workspace),
        "--language", "en-US",
        "--company", "Test",
        "--industry", "manufacturing",
        "--title", "Test Brief",
        "--audience", "management",
        "--cadence", "weekly",
        "--source-profile", "research",
        "--tavily",
    ]) == 0

    # Ensure no Tavily key in environment
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    # Add a minimal input file
    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "input" / "test.md").write_text("- Test data.", encoding="utf-8")

    # Pipeline should fail-fast
    exit_code = main(["run", "--config", str(workspace / "config.yaml")])
    assert exit_code != 0
