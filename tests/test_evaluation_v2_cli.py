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


def test_validate_fails_loudly_on_packaged_skeleton(capsys):
    # The shipped skeleton corpus (0 cases) must fail honestly, with its
    # counts on record: a valid verdict here would be a public claim
    # exceeding the artifacts.
    assert main(["eval", "validate", "--corpus", str(DEFAULT_CORPUS)]) == 1
    captured = capsys.readouterr()
    assert "cases: 0" in captured.out
    assert "split train: 0 cases, 0 blocking" in captured.out
    assert "split val: 0 cases, 0 blocking" in captured.out
    assert "corpus invalid" in captured.err


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


def test_run_fails_closed_without_adapter(tmp_path, capsys):
    manifest = _small_corpus(tmp_path)
    exit_code = main(
        ["eval", "run", "--corpus", str(manifest), "--split", "val"]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "no rollout adapter is available yet" in captured.err
    assert "codex adapter lands with the rollout task" in captured.err
    # No fake or placeholder reward may ever reach stdout.
    assert captured.out == ""


def test_run_fails_closed_even_on_valid_corpus(tmp_path, capsys):
    # The adapter check dominates: no rollout backend means no run, even for
    # a corpus that would pass validation.
    manifest = _full_scale_corpus(tmp_path)
    assert main(["eval", "run", "--corpus", str(manifest), "--split", "val"]) == 1
    assert "no rollout adapter is available yet" in capsys.readouterr().err


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
    monkeypatch.setattr(eval_commands, "_build_rollout", lambda: _perfect_rollout)

    assert (
        main(["eval", "run", "--corpus", str(manifest), "--split", "val", "--json"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["split"] == "val"
    assert payload["case_count"] == 40
    assert payload["defect_recall"] == 1.0
    assert payload["true_negative_rate"] == 1.0
    assert payload["block_agreement"] == 1.0
    assert payload["format_compliance"] == 1.0
    assert payload["reward"] == 1.0

    assert main(["eval", "run", "--corpus", str(manifest), "--split", "val"]) == 0
    out = capsys.readouterr().out
    assert "split            val" in out
    assert "format_compliance 1.0000" in out
    assert "R                1.0000" in out
