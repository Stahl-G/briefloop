from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_public_safety.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_public_safety_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_public_safety_scan_uses_env_banned_terms_without_repo_denylist(tmp_path):
    module = _load_module()
    sample = tmp_path / "sample.md"
    sample.write_text("Private release candidate mentions ACME_PRIVATE.\n", encoding="utf-8")

    findings = module.scan([sample], banned_terms=["ACME_PRIVATE"])

    assert len(findings) == 1
    assert findings[0].kind == "banned_term"
    assert findings[0].path == sample


def test_public_safety_scan_allows_fake_test_tokens(tmp_path):
    module = _load_module()
    module.ROOT = tmp_path
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    fake_path = test_dir / "fake_safety_fixture_for_unit_test.md"
    fake_path.write_text(
        "Example recipient oc_secret_chat and file:///Users/example/source.md # PUBLIC_SAFETY_TEST_FIXTURE\n",
        encoding="utf-8",
    )

    findings = module.scan([fake_path], banned_terms=[])

    assert findings == []


def test_public_safety_scan_allows_fake_test_tokens_by_path_component(tmp_path):
    module = _load_module()
    module.ROOT = tmp_path / "repo-root"
    test_dir = tmp_path / "outside" / "tests"
    test_dir.mkdir(parents=True)
    fake_path = test_dir / "fake_safety_fixture_for_unit_test.md"
    fake_path.write_text(
        "Example recipient oc_secret_chat and file:///Users/example/source.md # PUBLIC_SAFETY_TEST_FIXTURE\n",
        encoding="utf-8",
    )

    findings = module.scan([fake_path], banned_terms=[])

    assert findings == []


def test_public_safety_scan_catches_lark_recipient_and_file_token_prefixes(tmp_path):
    module = _load_module()
    sample = tmp_path / "candidate_pack.md"
    sample.write_text(
        "\n".join(
            [
                "folder fld1234567890abcdef",  # PUBLIC_SAFETY_TEST_FIXTURE
                "open message on1234567890abcdef",  # PUBLIC_SAFETY_TEST_FIXTURE
                "cli token cli1234567890abcdef",  # PUBLIC_SAFETY_TEST_FIXTURE
                "file token f1234567890abcdef",  # PUBLIC_SAFETY_TEST_FIXTURE
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    findings = module.scan([sample], banned_terms=[])

    assert [finding.kind for finding in findings] == [
        "lark_token",
        "lark_token",
        "lark_token",
        "lark_token",
    ]


def test_public_safety_scan_catches_no_digit_lark_token_shapes(tmp_path):
    module = _load_module()
    sample = tmp_path / "candidate_pack.md"
    sample.write_text(
        "\n".join(
            [
                "folder fldabcdefghijk",  # PUBLIC_SAFETY_TEST_FIXTURE
                "cli token cliabcdefghijk",  # PUBLIC_SAFETY_TEST_FIXTURE
                "file token fabcdefghijklmnop",  # PUBLIC_SAFETY_TEST_FIXTURE
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    findings = module.scan([sample], banned_terms=[])

    assert [finding.sample for finding in findings] == [
        "folder fldabcdefghijk",  # PUBLIC_SAFETY_TEST_FIXTURE
        "cli token cliabcdefghijk",  # PUBLIC_SAFETY_TEST_FIXTURE
        "file token fabcdefghijklmnop",  # PUBLIC_SAFETY_TEST_FIXTURE
    ]


def test_public_safety_scan_catches_bare_no_digit_lark_token_shapes(tmp_path):
    module = _load_module()
    sample = tmp_path / "candidate_pack.md"
    sample.write_text(
        "fldabcdefghijk\n"  # PUBLIC_SAFETY_TEST_FIXTURE
        "cliabcdefghijk\n",  # PUBLIC_SAFETY_TEST_FIXTURE
        encoding="utf-8",
    )

    findings = module.scan([sample], banned_terms=[])

    assert [finding.sample for finding in findings] == [
        "fldabcdefghijk",  # PUBLIC_SAFETY_TEST_FIXTURE
        "cliabcdefghijk",  # PUBLIC_SAFETY_TEST_FIXTURE
    ]


def test_public_safety_scan_does_not_flag_common_words_starting_with_token_prefixes(tmp_path):
    module = _load_module()
    sample = tmp_path / "public_docs.md"
    sample.write_text(
        "finalize formatter freshness file_path client onboarding folder\n",
        encoding="utf-8",
    )

    findings = module.scan([sample], banned_terms=[])

    assert findings == []


def test_public_safety_scan_does_not_flag_long_normal_f_words(tmp_path):
    module = _load_module()
    sample = tmp_path / "public_docs.md"
    sample.write_text(
        "\n".join(
            [
                "frameworkclassification",
                "freshnessclassification",
                "formatterconfiguration",
                "federalregulationupdate",
                "finaldeliveryartifact",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    findings = module.scan([sample], banned_terms=[])

    assert findings == []


def test_public_safety_scan_does_not_broadly_allow_tests_directory(tmp_path):
    module = _load_module()
    module.ROOT = tmp_path
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    leak_path = test_dir / "test_leak.py"
    leak_path.write_text(
        "SECRET = 'sk-123456789012345678901234'\n"  # PUBLIC_SAFETY_TEST_FIXTURE
        "URL = 'file:///Users/realuser/private.md'\n",  # PUBLIC_SAFETY_TEST_FIXTURE
        encoding="utf-8",
    )

    findings = module.scan([leak_path], banned_terms=[])

    assert sorted(finding.kind for finding in findings) == ["common_secret", "file_url", "user_path"]


def test_public_safety_allowlist_handles_windows_style_relative_paths(monkeypatch):
    module = _load_module()

    monkeypatch.setattr(
        module,
        "_relative",
        lambda path: "tests\\test_improvement_contract.py",
    )
    assert module._allowed_fixture(
        Path("ignored"),
        '"file:///Users/example/source.md",',  # PUBLIC_SAFETY_TEST_FIXTURE
        "file_url",
    )

    monkeypatch.setattr(
        module,
        "_relative",
        lambda path: "scripts\\check_public_safety.py",
    )
    assert module._allowed_fixture(
        Path("ignored"),
        'FILE_URL_RE = re.compile(r"file://[^\\s]+")',  # PUBLIC_SAFETY_TEST_FIXTURE
        "file_url",
    )


def test_public_safety_default_scan_falls_back_for_source_archives(tmp_path, monkeypatch):
    module = _load_module()
    module.ROOT = tmp_path
    (tmp_path / "README.md").write_text("PRIVATE_ARCHIVE_TERM\n", encoding="utf-8")
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "generated.md").write_text("PRIVATE_ARCHIVE_TERM\n", encoding="utf-8")
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "generated.py").write_text("PRIVATE_ARCHIVE_TERM\n", encoding="utf-8")

    def fail_git(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=128, cmd=kwargs.get("args") or "git ls-files")

    monkeypatch.setattr(module.subprocess, "run", fail_git)

    findings = module.scan(None, banned_terms=["PRIVATE_ARCHIVE_TERM"])

    assert [(finding.path.name, finding.kind) for finding in findings] == [
        ("README.md", "banned_term")
    ]


def test_public_safety_scan_covers_fast_rerun_public_fixtures():
    module = _load_module()
    fixtures = [
        ROOT / "tests" / "fixtures" / "fast_rerun_clean_archive",
        ROOT / "tests" / "fixtures" / "fast_rerun_source_candidates_only_archive",
    ]

    findings = module.scan(fixtures, banned_terms=[])

    assert findings == []
