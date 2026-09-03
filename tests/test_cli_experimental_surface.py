"""Experimental commands are hidden from default help but stay callable."""

from __future__ import annotations

import argparse

import pytest

from multi_agent_brief.cli.experimental import (
    EXPERIMENTAL_COMMANDS,
    experimental_enabled,
    hide_experimental_commands,
)


def _parser() -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    parser = argparse.ArgumentParser(prog="briefloop")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("keep", help="visible command")
    subparsers.add_parser("experiments", help="experimental harness")
    subparsers.add_parser("quality", help="experimental quality surface")
    return parser, subparsers


def test_hidden_commands_absent_from_help():
    parser, subparsers = _parser()
    hide_experimental_commands(subparsers)
    text = parser.format_help()
    assert "experiments" not in text
    assert "quality" not in text
    assert "keep" in text


def test_hidden_commands_remain_callable():
    parser, subparsers = _parser()
    hide_experimental_commands(subparsers)
    assert parser.parse_args(["experiments"]).command == "experiments"
    assert parser.parse_args(["quality"]).command == "quality"


def test_hiding_does_not_shrink_choices():
    parser, subparsers = _parser()
    before = set(subparsers.choices)
    hide_experimental_commands(subparsers)
    assert set(subparsers.choices) == before


def test_experimental_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("BRIEFLOOP_EXPERIMENTAL", raising=False)
    assert experimental_enabled() is False
    monkeypatch.setenv("BRIEFLOOP_EXPERIMENTAL", "1")
    assert experimental_enabled() is True
    monkeypatch.setenv("BRIEFLOOP_EXPERIMENTAL", "0")
    assert experimental_enabled() is False


def test_experimental_command_list_is_frozen():
    assert EXPERIMENTAL_COMMANDS == frozenset(
        {
            "experiments",
            "eval",
            "new",
            "packs",
            "validate-report-spec",
            "extract",
            "quality",
        }
    )


def test_hide_is_idempotent():
    parser, subparsers = _parser()
    hide_experimental_commands(subparsers)
    first = parser.format_help()
    hide_experimental_commands(subparsers)
    assert parser.format_help() == first


def test_hide_tolerates_absent_commands():
    parser = argparse.ArgumentParser(prog="briefloop")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("keep", help="visible command")
    hide_experimental_commands(subparsers)
    assert "keep" in parser.format_help()


def _command_subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if getattr(action, "dest", None) == "command" and hasattr(action, "choices"):
            return action
    raise AssertionError("command subparsers not found")


def test_build_parser_hides_experimental_by_default(monkeypatch):
    monkeypatch.delenv("BRIEFLOOP_EXPERIMENTAL", raising=False)
    from multi_agent_brief.cli.main import build_parser

    parser = build_parser()
    text = parser.format_help()
    for command in EXPERIMENTAL_COMMANDS:
        assert command not in text, f"{command} should be hidden by default"
    assert "status" in text
    assert "runtime" in text


def test_build_parser_shows_experimental_when_opted_in(monkeypatch):
    monkeypatch.setenv("BRIEFLOOP_EXPERIMENTAL", "1")
    from multi_agent_brief.cli.main import build_parser

    text = build_parser().format_help()
    for command in EXPERIMENTAL_COMMANDS:
        assert command in text, f"{command} should be visible when opted in"


def test_hidden_experimental_commands_still_registered(monkeypatch):
    monkeypatch.delenv("BRIEFLOOP_EXPERIMENTAL", raising=False)
    from multi_agent_brief.cli.main import build_parser

    subparsers = _command_subparsers(build_parser())
    for command in EXPERIMENTAL_COMMANDS:
        assert command in subparsers.choices
