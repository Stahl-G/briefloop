from __future__ import annotations

from pathlib import Path

from multi_agent_brief.outputs.naming import render_output_stem, sanitize_filename_stem


def test_sanitize_filename_stem_preserves_readable_chinese_title():
    stem = sanitize_filename_stem('ExampleCo 光储周报: 2026/06/04?.md')
    assert stem == "ExampleCo_光储周报_2026_06_04"


def test_render_output_stem_uses_safe_missing_tokens():
    stem = render_output_stem(
        "{company}_{project_name}_{report_date}_{missing}",
        {
            "company": "ExampleCo",
            "project_name": "Weekly Brief",
            "report_date": "2026-06-04",
        },
    )
    assert stem == "ExampleCo_Weekly_Brief_2026-06-04"


def test_build_run_settings_reads_output_filename_template(tmp_path):
    from multi_agent_brief.core.config import build_run_settings

    config = {
        "project": {
            "name": "ExampleCo 光储周报",
            "company": "ExampleCo",
            "industry": "storage",
        },
        "report": {"date": "2026-06-04", "cadence": "weekly"},
        "input": {"path": str(tmp_path / "input")},
        "output": {
            "path": str(tmp_path / "output"),
            "filename_template": "{company}_{project_name}_{report_date}",
            "named_outputs": True,
        },
    }

    settings = build_run_settings(
        config=config,
        input_dir=None,
        output_dir=None,
        name=None,
        language=None,
        audience=None,
    )

    assert settings["output_filename_template"] == "{company}_{project_name}_{report_date}"
    assert settings["output_filename_tokens"]["company"] == "ExampleCo"
    assert settings["output_filename_tokens"]["cadence"] == "weekly"
    assert settings["output_named_outputs"] is True


def test_build_run_settings_allows_string_false_for_named_outputs(tmp_path):
    from multi_agent_brief.core.config import build_run_settings

    config = {
        "input": {"path": str(tmp_path / "input")},
        "output": {
            "path": str(tmp_path / "output"),
            "named_outputs": "false",
        },
    }

    settings = build_run_settings(
        config=config,
        input_dir=None,
        output_dir=None,
        name=None,
        language=None,
        audience=None,
    )

    assert settings["output_named_outputs"] is False


def test_formatter_writes_named_reader_markdown(tmp_path):
    from multi_agent_brief.agents.formatter import FormatterAgent
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import PipelineContext, ReportState

    context = PipelineContext(
        project_name="ExampleCo 光储周报",
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output"),
        report_date="2026-06-04",
        output_filename_template="{project_name}_{report_date}",
        report_state=ReportState(
            prepared_markdown="# ExampleCo 光储周报\n\n- Revenue grew 5%. [src:CLAIM_123456]"
        ),
    )

    result = FormatterAgent().run(context, ClaimLedger())

    named = Path(result.artifacts["brief_named"])
    stable = tmp_path / "output" / "brief.md"
    audited = tmp_path / "output" / "intermediate" / "audited_brief.md"

    assert named.name == "ExampleCo_光储周报_2026-06-04.md"
    assert named.exists()
    assert named.read_text(encoding="utf-8") == stable.read_text(encoding="utf-8")
    assert "[src:" not in named.read_text(encoding="utf-8")
    assert "[src:" in audited.read_text(encoding="utf-8")


def test_formatter_can_disable_named_outputs(tmp_path):
    from multi_agent_brief.agents.formatter import FormatterAgent
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import PipelineContext, ReportState

    context = PipelineContext(
        project_name="No Named Copy",
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output"),
        output_named_outputs=False,
        report_state=ReportState(prepared_markdown="# Brief\n\nContent."),
    )

    result = FormatterAgent().run(context, ClaimLedger())

    assert "brief_named" not in result.artifacts
    assert not (tmp_path / "output" / "No_Named_Copy.md").exists()
