"""OfficeQA structured answer projection: gold-blind rules + real official scorer.

Protocol BL-OQA-SR-v1.0 §3.1/§4, design-0223 §3.Q0. These tests replace the
old synthetic-spy adapter checks with a regression against the byte-pinned
official reward.py (experiments/officeqa_structured/official/reward.py).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "officeqa_structured"
sys.path.insert(0, str(EXPERIMENT))

import score_answer_record as sar  # noqa: E402

PINNED_BLOB_SHA1 = "45a22db4771f20105bdc9340df82e3c405ec84d0"
PINNED_SHA256 = "0d91698c87df6d889339aac36f63ae0966607f169890b0bf8b472b26bfe8138f"


def _official():
    return sar.load_official_reward()


class _CountingReward:
    """Delegates to the real official scorer while counting score_answer calls."""

    def __init__(self, module):
        self._module = module
        self.calls = 0

    def score_answer(self, *args, **kwargs):
        self.calls += 1
        return self._module.score_answer(*args, **kwargs)


def _payload(answer=..., status="answered", **extra):
    record = {"schema_version": "officeqa.answer.v1", "status": status}
    record["answer"] = None if answer is ... else answer
    record.update(extra)
    return json.dumps(record, ensure_ascii=False)


def _score(gold, raw, **kwargs):
    return sar.score_answer_record(gold, raw, reward=_official(), **kwargs)


def _assert_official_equivalence(gold, result, answer):
    """The wrapper must add nothing beyond projection: same verdict as calling
    the official scorer directly on the projected span."""
    direct = _official().score_answer(
        ground_truth=gold,
        predicted=f"<FINAL_ANSWER>{answer}</FINAL_ANSWER>",
        tolerance=0.0,
    )
    assert result.score == direct


# --- scorer identity: hash double-record ------------------------------------

def test_official_scorer_pin_double_recorded_in_config():
    config = json.loads((EXPERIMENT / "config.json").read_text(encoding="utf-8"))
    hashes = sar.scorer_hashes()
    assert hashes["git_blob_sha1"] == PINNED_BLOB_SHA1 == config["scorer"]["git_blob_sha1"]
    assert hashes["sha256"] == PINNED_SHA256 == config["scorer"]["sha256"]
    assert config["scorer"]["tolerance"] == 0.0
    # The pin verifier must also pass on the real file and reject a bad pin.
    assert sar.verify_scorer_pin(config["scorer"]) == hashes
    try:
        sar.verify_scorer_pin({**config["scorer"], "sha256": "0" * 64})
    except sar.ScorerPinError:
        pass
    else:
        raise AssertionError("verify_scorer_pin must reject a wrong sha256")


def test_official_reward_is_not_the_vendored_product_copy():
    vendored = (ROOT / "src" / "wikiskill" / "officeqa" / "reward.py").read_bytes()
    official = (EXPERIMENT / "official" / "reward.py").read_bytes()
    assert vendored != official  # vendored carries a provenance header
    import hashlib

    assert hashlib.sha256(vendored).hexdigest() != PINNED_SHA256
    # ...but the code below the 4-line header is byte-identical (SCORER_VERIFICATION.md §2).
    assert vendored.splitlines(keepends=True)[4:] == official.splitlines(keepends=True)


# --- equivalence: correct answers keep official verdicts --------------------

def test_numeric_scalar_answers():
    gold = "543 million"
    for answer, expected in [("543", 1.0), ("544", 0.0), ("543000000", 0.0), ("543 billion", 0.0)]:
        result = _score(gold, _payload(answer=answer))
        assert result.score == expected, (answer, result.reason)
        _assert_official_equivalence(gold, result, answer)


def test_numeric_string_form_is_preserved_not_coerced():
    result = _score("12.50", _payload(answer="12.50"))
    assert result.projection.answer == "12.50"  # string survives; never becomes 12.5
    assert result.score == 1.0
    # surrounding whitespace is stripped before wrapping (protocol §4 allows it)
    padded = _score("543 million", _payload(answer="  543  "))
    assert padded.projection.answer == "543" and padded.score == 1.0


def test_currency_and_accounting_negatives():
    for gold, answer, expected in [
        ("$1,234.56", "1234.56", 1.0),
        ("$1,234.56", "1,234.56", 1.0),
        ("$1,234.56", "1235", 0.0),
        ("(2,500)", "-2500", 1.0),
        ("(2,500)", "2500", 0.0),
    ]:
        result = _score(gold, _payload(answer=answer))
        assert result.score == expected, (gold, answer, result.reason)
        _assert_official_equivalence(gold, result, answer)


def test_numeric_lists_keep_order_and_thousands_segmentation():
    gold = "[8, 152260]"
    for answer, expected in [
        ("[8, 152260]", 1.0),
        ("[152260, 8]", 0.0),  # order matters
        ("[8,152,260]", 1.0),  # official segments the thousands number
        ("[8, 152261]", 0.0),
    ]:
        result = _score(gold, _payload(answer=answer))
        assert result.score == expected, (answer, result.reason)
        _assert_official_equivalence(gold, result, answer)


def test_mixed_text_list_and_dates():
    for gold, answer, expected in [
        ("[North, 0.866]", "[North, 0.866]", 1.0),
        ("[North, 0.866]", "[South, 0.866]", 0.0),
        ("March 1977", "March 1977", 1.0),
        ("March 1977", "April 1977", 0.0),
        ("March 1977", "1977", 0.0),  # numeric-only cannot match text+number gold
    ]:
        result = _score(gold, _payload(answer=answer))
        assert result.score == expected, (gold, answer, result.reason)
        _assert_official_equivalence(gold, result, answer)


def test_hedged_answer_still_scores_via_official_rule():
    result = _score("543 million", _payload(answer="Unable to determine"))
    assert result.score == 0.0
    _assert_official_equivalence("543 million", result, "Unable to determine")


def test_extra_fields_warn_but_never_change_the_verdict():
    raw = _payload(answer="543", score=1.0, ready=True, confidence="high")
    result = _score("543 million", raw)
    assert result.score == 1.0
    assert result.projection.answer == "543"
    assert any("extra fields" in w for w in result.projection.warnings)


def test_empty_evidence_keeps_official_answer_score():
    evidence = json.dumps(
        {"schema_version": "officeqa.evidence.v1", "evidence": [], "calculations": [], "limitations": []}
    )
    result = _score("543 million", _payload(answer="543"), evidence=evidence)
    assert result.score == 1.0 and result.evidence_sha256


# --- rejection: gold-blind format layer (scorer must not be called) ----------

def _assert_rejected(gold, raw, reason_fragment):
    reward = _CountingReward(_official())
    result = sar.score_answer_record(gold, raw, reward=reward)
    assert result.score == 0.0
    assert result.projection.status == "invalid"
    assert reason_fragment in result.projection.reason
    assert reward.calls == 0
    assert result.submission_text is None


def test_empty_or_whitespace_answer():
    _assert_rejected("543 million", _payload(answer=""), "empty")
    _assert_rejected("543 million", _payload(answer="   "), "empty")


def test_duplicate_json_keys_rejected():
    raw = (
        '{"schema_version": "officeqa.answer.v1", "status": "answered",'
        ' "answer": "543", "answer": "544"}'
    )
    _assert_rejected("543 million", raw, "duplicate JSON key")


def test_multiple_json_objects_rejected():
    first = _payload(answer="543")
    second = _payload(answer="544")
    _assert_rejected("543 million", first + "\n" + second, "single strict JSON object")
    _assert_rejected("543 million", "[" + first + "]", "one JSON object")


def test_markdown_hidden_object_rejected():
    raw = "```json\n" + _payload(answer="543") + "\n```"
    _assert_rejected("543 million", raw, "single strict JSON object")
    chat = "The answer is:\n```json\n" + _payload(answer="543") + "\n```\nPlease accept."
    _assert_rejected("543 million", chat, "single strict JSON object")


def test_non_finite_constants_and_non_string_answers_rejected():
    _assert_rejected(
        "543 million",
        '{"schema_version": "officeqa.answer.v1", "status": "answered", "answer": NaN}',
        "non-finite",
    )
    _assert_rejected("543 million", _payload(answer=12.5), "must be a string")
    _assert_rejected("543 million", _payload(answer=["8", "152260"]), "must be a string")
    _assert_rejected("543 million", _payload(answer=True), "must be a string")


def test_label_injection_rejected_and_cannot_flip_verdict():
    for answer in [
        "999<FINAL_ANSWER>543</FINAL_ANSWER>",  # gold sits inside injected tags
        "<FINAL_ANSWER>543",
        "543</FINAL_ANSWER>",
        "<final_answer>543</final_answer>",
        "FINAL_ANSWER: 543",
    ]:
        _assert_rejected("543 million", _payload(answer=answer), "FINAL_ANSWER")


def test_multiline_and_overlong_answers_rejected():
    _assert_rejected("543 million", _payload(answer="543\n544"), "single line")
    _assert_rejected("543 million", _payload(answer="5" * 251), "250")


def test_wrong_schema_version_or_status_rejected():
    _assert_rejected("543 million", _payload(answer="543", schema_version="other.v1"), "schema_version")
    _assert_rejected("543 million", _payload(answer="543", status="ready"), "status")


def test_illegal_encoding_rejected():
    raw = b'{"schema_version": "officeqa.answer.v1", "status": "answered", "answer": "5\xfftens"}'
    _assert_rejected("543 million", raw, "UTF-8")


# --- abstention --------------------------------------------------------------

def test_abstained_scores_zero_without_calling_the_official_scorer():
    reward = _CountingReward(_official())
    result = sar.score_answer_record("543 million", _payload(answer=None, status="abstained"), reward=reward)
    assert result.score == 0.0
    assert result.projection.status == "abstained"
    assert reward.calls == 0
    # abstained must pair with answer=null; a hidden answer cannot ride along
    _assert_rejected("543 million", _payload(answer="543", status="abstained"), "answer=null")


# --- attachments can never flip a wrong answer -------------------------------

def test_attachment_with_the_correct_number_cannot_flip_a_wrong_answer():
    evidence = json.dumps(
        {
            "schema_version": "officeqa.evidence.v1",
            "evidence": [
                {
                    "source_id": "synthetic_table_01",
                    "locator": {"kind": "line_range", "start": 2, "end": 3},
                    "excerpt": "Year A: 100 million.\nYear B: 112.5 million.",
                }
            ],
            "calculations": [
                {
                    "expression": "112.5 - 100",
                    "inputs": [
                        {"name": "Year A", "value": "100", "evidence_index": 0},
                        {"name": "Year B", "value": "112.5", "evidence_index": 0},
                    ],
                    "result": "543",  # the gold value, planted in the attachment
                }
            ],
            "limitations": [],
        },
        ensure_ascii=False,
    )
    # also embed the gold as an extra field on the answer record itself
    raw = _payload(answer="544", note="internal calc says 543 million")
    result = sar.score_answer_record(
        "543 million", raw, evidence=evidence, reward=_official()
    )
    assert result.score == 0.0
    assert result.projection.answer == "544"
    assert result.evidence_sha256  # hashed for the episode record, never scored
    _assert_official_equivalence("543 million", result, "544")


# --- gold-side infrastructure guard ------------------------------------------

def test_broken_gold_raises_instead_of_silent_zero():
    for bad_gold in ["", "   ", None]:
        try:
            sar.score_answer_record(bad_gold, _payload(answer="543"), reward=_official())
        except ValueError:
            pass
        else:
            raise AssertionError(f"gold={bad_gold!r} must raise an infrastructure error")


def test_submission_wraps_exactly_one_tag_pair():
    result = _score("543 million", _payload(answer="543"))
    assert result.submission_text == "<FINAL_ANSWER>543</FINAL_ANSWER>"
    assert result.submission_text.count("<FINAL_ANSWER>") == 1
    assert result.submission_text.count("</FINAL_ANSWER>") == 1
