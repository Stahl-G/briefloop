from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.agents.draft_cleanup import strip_claim_citations
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext
from multi_agent_brief.outputs.naming import render_output_stem
from multi_agent_brief.outputs.source_map import render_source_map

logger = logging.getLogger(__name__)


class FormatterAgent(BaseAgent):
    name = "formatter"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        output_dir = Path(context.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Intermediate artifacts go into output/intermediate/
        intermediate_dir = output_dir / "intermediate"
        intermediate_dir.mkdir(parents=True, exist_ok=True)

        # Final reader-facing brief stays at output/brief.md and omits internal
        # claim IDs. The audited version with [src:CLAIM_ID] stays internal.
        brief_path = output_dir / "brief.md"
        # Intermediate artifacts
        audited_path = intermediate_dir / "audited_brief.md"
        draft_path = intermediate_dir / "draft_brief.md"
        ledger_path = intermediate_dir / "claim_ledger.json"
        audit_path = intermediate_dir / "audit_report.json"
        source_map_path = intermediate_dir / "source_map.md"

        audited_markdown = context.report_state.prepared_markdown
        reader_markdown = strip_claim_citations(audited_markdown)

        brief_path.write_text(reader_markdown, encoding="utf-8")
        named_stem = self._named_output_stem(context)
        named_brief_path = output_dir / f"{named_stem}.md" if named_stem else None
        if named_brief_path and named_brief_path != brief_path:
            named_brief_path.write_text(reader_markdown, encoding="utf-8")
        audited_path.write_text(audited_markdown, encoding="utf-8")
        draft_path.write_text(context.report_state.draft_markdown, encoding="utf-8")
        ledger.export_json(ledger_path)
        source_map_path.write_text(render_source_map(ledger), encoding="utf-8")

        artifacts: dict[str, str] = {
            "brief": str(brief_path),
            "audited_brief": str(audited_path),
            "draft_brief": str(draft_path),
            "claim_ledger": str(ledger_path),
            "source_map": str(source_map_path),
        }
        if named_brief_path and named_brief_path != brief_path:
            artifacts["brief_named"] = str(named_brief_path)

        # DOCX output — only if "docx" is in output_formats.
        # Must run BEFORE writing audit_report.json so docx_generation
        # metadata is included in the persisted file.
        docx_status = None
        if "docx" in (context.output_formats or []):
            docx_path = output_dir / "brief.docx"
            try:
                from multi_agent_brief.outputs.ib_docx import convert

                convert(
                    brief_path,
                    docx_path,
                    title=context.project_name,
                    footer=context.output_footer or None,
                )
                artifacts["brief_docx"] = str(docx_path)
                if named_stem and named_stem != "brief":
                    named_docx_path = output_dir / f"{named_stem}.docx"
                    shutil.copyfile(docx_path, named_docx_path)
                    artifacts["brief_docx_named"] = str(named_docx_path)
                docx_status = "generated"
            except ImportError:
                logger.warning(
                    "python-docx is not installed. "
                    "Install it with: pip install 'multi-agent-brief-workflow[docx]'"
                )
                docx_status = "skipped_missing_dependency"
            except Exception:
                logger.exception("DOCX generation failed")
                docx_status = "failed"

        # Record docx generation status in audit report metadata
        audit_report = context.report_state.audit_report
        if audit_report:
            if docx_status:
                audit_report.metadata["docx_generation"] = docx_status
            audit_report.metadata["audited_markdown_artifact"] = str(audited_path)
            audit_report.metadata["reader_brief_artifact"] = str(brief_path)
            audit_report.metadata["reader_brief_transform"] = "strip_claim_citations"
            if named_brief_path and named_brief_path != brief_path:
                audit_report.metadata["named_reader_brief_artifact"] = str(named_brief_path)
            # Write audit_report.json AFTER docx status is set
            audit_path.write_text(
                json.dumps(audit_report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            artifacts["audit_report"] = str(audit_path)

        return AgentOutput(
            agent_name=self.name,
            summary=f"Wrote artifacts to {output_dir}.",
            artifacts=artifacts,
        )

    def _named_output_stem(self, context: PipelineContext) -> str:
        if not context.output_named_outputs:
            return ""
        tokens = {
            "project_name": context.project_name,
            "title": context.project_name,
            "report_date": context.report_date,
            "date": context.report_date,
            "language": context.language,
            "audience": context.audience,
        }
        tokens.update(context.output_filename_tokens)
        return render_output_stem(context.output_filename_template, tokens)
