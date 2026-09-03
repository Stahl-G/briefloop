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
        {"experiments", "new", "packs", "validate-report-spec", "extract", "quality"}
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
