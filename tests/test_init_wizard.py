from argparse import Namespace

from multi_agent_brief.cli.init_wizard import build_profile_from_args, prompt_for_profile
from multi_agent_brief.cli.main import main


def test_init_demo_preserves_existing_demo_behavior(tmp_path):
    demo_dir = tmp_path / "demo"

    assert main(["init", str(demo_dir), "--demo"]) == 0

    assert (demo_dir / "config.yaml").exists()
    assert (demo_dir / "input" / "news.json").exists()
    assert (demo_dir / "input" / "market_data.json").exists()


def test_init_workspace_creates_expected_files(tmp_path):
    workspace = tmp_path / "workspace"

    assert main(["init", str(workspace), "--language", "zh-CN"]) == 0

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

    main(["init", str(workspace), "--language", "zh-CN"])

    gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
    for expected in [".env", "output/", ".rag/", "private_inputs/", "private_outputs/"]:
        assert expected in gitignore


def test_generated_input_readme_is_not_treated_as_source(tmp_path):
    workspace = tmp_path / "workspace"

    main(["init", str(workspace), "--language", "zh-CN"])
    assert main(["run", "--config", str(workspace / "config.yaml")]) == 0

    ledger = (workspace / "output" / "claim_ledger.json").read_text(encoding="utf-8")
    assert ledger.strip() == "[]"


def test_init_workspace_creates_user_md(tmp_path):
    workspace = tmp_path / "workspace"
    assert main(["init", str(workspace), "--language", "zh-CN"]) == 0
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
        "--audience", "management",
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
    assert main([
        "init", str(workspace),
        "--language", "en-US",
        "--source-profile", "llm_decide",
    ]) == 0
    # Verify source_candidates.yaml is NOT created at init time
    assert not (workspace / "source_candidates.yaml").exists()


def test_llm_decide_user_md_not_treated_as_source(tmp_path):
    workspace = tmp_path / "workspace"
    main(["init", str(workspace), "--language", "zh-CN", "--source-profile", "llm_decide"])
    assert main(["run", "--config", str(workspace / "config.yaml")]) == 0
    ledger = (workspace / "output" / "claim_ledger.json").read_text(encoding="utf-8")
    assert ledger.strip() == "[]"


def test_source_profile_noninteractive_arg_is_respected(tmp_path):
    workspace = tmp_path / "workspace"
    main(["init", str(workspace), "--language", "en-US", "--source-profile", "llm_decide"])
    config = (workspace / "config.yaml").read_text(encoding="utf-8")
    assert 'profile: "llm_decide"' in config
    sources = (workspace / "sources.yaml").read_text(encoding="utf-8")
    assert 'profile: "llm_decide"' in sources


def test_all_source_profiles_generate_valid_workspace(tmp_path):
    for profile in ["conservative", "research", "aggressive_signal", "custom", "llm_decide"]:
        workspace = tmp_path / f"ws-{profile}"
        assert main(["init", str(workspace), "--language", "zh-CN", "--source-profile", profile]) == 0
        assert (workspace / "config.yaml").exists()
        assert (workspace / "sources.yaml").exists()
        assert (workspace / "user.md").exists()
        assert (workspace / "input" / "README.md").exists()
