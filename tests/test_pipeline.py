from pathlib import Path

from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext


def test_pipeline_writes_outputs(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "news.md").write_text(
        "- A competitor announced a 2 GW manufacturing expansion plan.\n",
        encoding="utf-8",
    )

    context = PipelineContext(
        project_name="Demo Brief",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        report_date="2026-06-02",
        max_source_age_days=14,
        fail_on_stale_source=True,
    )

    outputs = BriefPipeline().run(context)

    assert len(outputs) == 6
    assert (output_dir / "brief.md").exists()
    assert (output_dir / "claim_ledger.json").exists()
    assert (output_dir / "audit_report.json").exists()
    assert (output_dir / "source_map.md").exists()
    assert "Demo Brief" in (output_dir / "brief.md").read_text(encoding="utf-8")
