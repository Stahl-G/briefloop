
import importlib.util
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






def test_public_safety_scan_does_not_flag_common_words_starting_with_token_prefixes(tmp_path):
    module = _load_module()
    sample = tmp_path / "public_docs.md"
    sample.write_text(
        "finalize formatter freshness file_path client onboarding folder\n",
        encoding="utf-8",
    )

    findings = module.scan([sample], banned_terms=[])

    assert findings == []




def test_public_safety_scan_allows_sha256_hex_lines(tmp_path):
    module = _load_module()
    sample = tmp_path / "protocol.yaml"
    sample.write_text(
        "sha256: f613f8fed53e5a414d29fef819018ffc4e2bebf0ddd145ddbabda3c295e4b540\n",
        encoding="utf-8",
    )

    findings = module.scan([sample], banned_terms=[])

    assert findings == []






















def test_public_safety_scan_covers_fast_rerun_public_fixtures():
    module = _load_module()
    fixtures = [
        ROOT / "tests" / "fixtures" / "fast_rerun_clean_archive",
        ROOT / "tests" / "fixtures" / "fast_rerun_source_candidates_only_archive",
    ]

    findings = module.scan(fixtures, banned_terms=[])

    assert findings == []
