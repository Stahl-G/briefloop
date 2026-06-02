from __future__ import annotations

from pathlib import Path

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext
from multi_agent_brief.outputs.source_map import render_source_map


class FormatterAgent(BaseAgent):
    name = "formatter"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        output_dir = Path(context.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        brief_path = output_dir / "brief.md"
        ledger_path = output_dir / "claim_ledger.json"
        audit_path = output_dir / "audit_report.json"
        source_map_path = output_dir / "source_map.md"

        brief_path.write_text(context.report_state.final_markdown, encoding="utf-8")
        ledger.export_json(ledger_path)

        audit_report = context.report_state.audit_report
        if audit_report:
            import json

            audit_path.write_text(json.dumps(audit_report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        source_map_path.write_text(render_source_map(ledger), encoding="utf-8")

        return AgentOutput(
            agent_name=self.name,
            summary=f"Wrote outputs to {output_dir}.",
            artifacts={
                "brief": str(brief_path),
                "claim_ledger": str(ledger_path),
                "audit_report": str(audit_path),
                "source_map": str(source_map_path),
            },
        )

