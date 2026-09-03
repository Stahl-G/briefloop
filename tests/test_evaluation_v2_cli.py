"""The experimental eval command: registration, hiding, and fail-closed run.

``eval validate`` is functional and honest: it prints the corpus composition
before the verdict, so even the packaged skeleton (0 cases) fails loudly with
its counts on record.  ``eval run`` fails closed -- no rollout backend exists
yet and no placeholder reward may ever be printed.  The downstream run shape
is still exercised here by injecting a rollout through the same single seam
(``_build_rollout``) the real codex adapter will fill; the reward then comes
from the real scoring code, never from the CLI.
"""

from __future__ import annotations

import argparse
import json

import pytest

from multi_agent_brief.cli import eval_commands
from multi_agent_brief.cli.experimental import EXPERIMENTAL_COMMANDS
from multi_agent_brief.cli.main import build_parser, main
from multi_agent_brief.evaluation_v2.contracts import (
    EvaluationCase,
    RolloutOutcome,
)
from multi_agent_brief.evaluation_v2.corpus import DEFAULT_CORPUS
from tests.test_evaluation_v2_corpus import (
    _blocking_payload,
    _clean_payload,
    _spread,
    _write_corpus,
)


def _small_corpus(tmp_path):
    """A well-formed but far-below-scale corpus: loads, cannot validate."""
    return _write_corpus(
        tmp_path,
        [
            ("b_train", "train", _blocking_payload("b_train")),
            ("c_val", "val", _clean_payload("c_val")),
        ],
    )


def _full_scale_corpus(tmp_path):
    return _write_corpus(tmp_path, _spread("train", 24, 16) + _spread("val", 24, 16))


def _perfect_rollout(case: EvaluationCase) -> RolloutOutcome:
    """Report every seeded defect exactly and block exactly when required."""
    return RolloutOutcome.model_validate(
        {
            "case_id": case.case_id,
            "found_defect_ids": [d.defect_id for d in case.seeded_defects],
            "flagged_claim_locators": [],
            "blocked": case.must_block,
            "findings": [
                {
                    "finding_type": d.finding_type,
                    "locator": d.locator,
                    "blocking_level": d.expected_blocking_level,
                }
                for d in case.seeded_defects
            ],
        },
        strict=True,
    )


def _command_subparsers(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction:
    for action in parser._actions:
        if getattr(action, "dest", None) == "command" and hasattr(action, "choices"):
            return action
    raise AssertionError("command subparsers not found")


# ---------------------------------------------------------------------------
# Registration and the experimental surface
# ---------------------------------------------------------------------------


def test_eval_is_registered_and_experimental():
    subparsers = _command_subparsers(build_parser())
    assert "eval" in subparsers.choices
    assert "eval" in EXPERIMENTAL_COMMANDS


def test_eval_hidden_from_default_help(monkeypatch):
    monkeypatch.delenv("BRIEFLOOP_EXPERIMENTAL", raising=False)
    assert "eval" not in build_parser().format_help()


def test_eval_visible_in_experimental_help(monkeypatch):
    monkeypatch.setenv("BRIEFLOOP_EXPERIMENTAL", "1")
    assert "eval" in build_parser().format_help()


def test_hidden_eval_still_callable_and_dispatchable(tmp_path):
    # Hiding removes the help entry, never the command itself: parsing
    # ["eval", ...] must yield the namespace the dispatch block routes on.
    manifest = _small_corpus(tmp_path)
    args = build_parser().parse_args(
        ["eval", "validate", "--corpus", str(manifest)]
    )
    assert args.command == "eval"
    assert args.eval_action == "validate"
    assert args.corpus == str(manifest)


# ---------------------------------------------------------------------------
# eval validate
# ---------------------------------------------------------------------------


def test_validate_reports_threshold_deficit(tmp_path, capsys):
    # Thresholds cannot be lowered from the CLI, so a small corpus must fail
    # with the deficit named.
    manifest = _small_corpus(tmp_path)
    assert main(["eval", "validate", "--corpus", str(manifest)]) == 1
    captured = capsys.readouterr()
    assert "at least 80 cases" in captured.err
    assert "corpus invalid" in captured.err
    assert "cases: 2" in captured.out


def test_validate_accepts_packaged_corpus(capsys):
    # The shipped corpus is the real 80-case measurement substrate; the CLI
    # reports its factual composition and passes production thresholds.
    assert main(["eval", "validate", "--corpus", str(DEFAULT_CORPUS)]) == 0
    captured = capsys.readouterr()
    assert "cases: 80" in captured.out
    assert "split train: 40 cases" in captured.out
    assert "split val: 40 cases" in captured.out
    assert "result: valid" in captured.out


def test_validate_accepts_full_scale_corpus(tmp_path, capsys):
    manifest = _full_scale_corpus(tmp_path)
    assert main(["eval", "validate", "--corpus", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "cases: 80" in out
    assert "split train: 40 cases, 24 blocking" in out
    assert "split val: 40 cases, 24 blocking" in out
    assert "result: valid" in out


def test_validate_reports_load_errors_to_stderr(tmp_path, capsys):
    missing = tmp_path / "nope.yaml"
    assert main(["eval", "validate", "--corpus", str(missing)]) == 1
    captured = capsys.readouterr()
    assert "corpus invalid" in captured.err
    assert str(missing) in captured.err


# ---------------------------------------------------------------------------
# eval run: fail closed until the rollout adapter lands
# ---------------------------------------------------------------------------


def test_run_fails_closed_without_adapter(tmp_path, capsys, monkeypatch):
    from multi_agent_brief.cli.eval_commands import RolloutAdapterUnavailable

    def _unavailable(**_kwargs):
        raise RolloutAdapterUnavailable("no rollout adapter is available")

    monkeypatch.setattr(
        "multi_agent_brief.cli.eval_commands._build_rollout", _unavailable
    )
    manifest = _small_corpus(tmp_path)
    exit_code = main(
        ["eval", "run", "--corpus", str(manifest), "--split", "val"]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "no rollout adapter is available" in captured.err
    # No fake or placeholder reward may ever reach stdout.
    assert captured.out == ""


def test_run_refuses_invalid_corpus_before_any_rollout(tmp_path, capsys, monkeypatch):
    # Corpus validation dominates: an under-threshold corpus refuses before
    # the rollout runs (the adapter is present, but must never be invoked).
    def _must_not_be_invoked(_case):
        raise AssertionError("rollout must not run on an invalid corpus")

    monkeypatch.setattr(
        "multi_agent_brief.cli.eval_commands._build_rollout",
        lambda **_kwargs: _must_not_be_invoked,
    )
    manifest = _small_corpus(tmp_path)
    assert main(["eval", "run", "--corpus", str(manifest), "--split", "val"]) == 1
    assert "corpus invalid" in capsys.readouterr().err


def test_run_requires_split_choice(tmp_path):
    manifest = _small_corpus(tmp_path)
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["eval", "run", "--corpus", str(manifest)])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(
            ["eval", "run", "--corpus", str(manifest), "--split", "holdout"]
        )
    assert excinfo.value.code == 2


def test_run_with_injected_rollout_scores_and_reports(
    tmp_path, capsys, monkeypatch
):
    # Exercise the full run shape through the single seam the real adapter
    # will fill; the reward is computed by the real scoring code.
    manifest = _full_scale_corpus(tmp_path)
    ledger = tmp_path / "reward_ledger.jsonl"
    monkeypatch.setattr(
        eval_commands, "_build_rollout", lambda **_kwargs: _perfect_rollout
    )
    run_args = [
        "eval", "run", "--corpus", str(manifest), "--split", "val",
        "--ledger", str(ledger),
    ]

    assert main(run_args + ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["split"] == "val"
    assert payload["case_count"] == 40
    assert payload["defect_recall"] == 1.0
    assert payload["true_negative_rate"] == 1.0
    assert payload["block_agreement"] == 1.0
    assert payload["format_compliance"] == 1.0
    assert payload["reward"] == 1.0

    assert main(run_args) == 0
    out = capsys.readouterr().out
    assert "split            val" in out
    assert "format_compliance 1.0000" in out
    assert "R                1.0000" in out
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    from multi_agent_brief.evaluation_v2.reward_ledger import load_records

    records = load_records(ledger)
    assert all(record.reward == 1.0 for record in records)
    assert all(len(record.corpus_sha256) == 64 for record in records)
