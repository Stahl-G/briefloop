from __future__ import annotations

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.agents.draft_cleanup import clean_process_residue
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext


class EditorAgent(BaseAgent):
    name = "editor"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        draft = context.report_state.draft_markdown
        # Remove process residue ([SRC:], [SOURCE:], empty [src:], Claude/Codex logs).
        # Valid [src:CLAIM_ID] citations are intentionally PRESERVED so the final
        # brief remains traceable to Claim Ledger and auditable.
        cleaned = clean_process_residue(draft)
        context.report_state.prepared_markdown = cleaned

        # Run Final Clean gate — detects issues but does NOT auto-remove them.
        # The gate produces a report that Formatter will persist.
        from multi_agent_brief.audit.final_quality import FinalCleanAuditAgent, FinalCleanConfig

        final_clean_config = FinalCleanConfig()
        # Allow config overrides from context metadata
        fc_config_data = context.metadata.get("final_clean", {})
        if fc_config_data:
            final_clean_config = FinalCleanConfig(
                enabled=fc_config_data.get("enabled", final_clean_config.enabled),
                check_template_variables=fc_config_data.get("check_template_variables", final_clean_config.check_template_variables),
                check_internal_paths=fc_config_data.get("check_internal_paths", final_clean_config.check_internal_paths),
                check_model_phrases=fc_config_data.get("check_model_phrases", final_clean_config.check_model_phrases),
                check_feedback_leakage=fc_config_data.get("check_feedback_leakage", final_clean_config.check_feedback_leakage),
                check_editorial_comments=fc_config_data.get("check_editorial_comments", final_clean_config.check_editorial_comments),
                check_investment_advice=fc_config_data.get("check_investment_advice", final_clean_config.check_investment_advice),
                check_invalid_citations=fc_config_data.get("check_invalid_citations", final_clean_config.check_invalid_citations),
            )

        final_clean_agent = FinalCleanAuditAgent(final_clean_config)
        final_clean_report = final_clean_agent.run_audit(cleaned, ledger, context)
        context.report_state.final_clean_report = final_clean_report.to_dict()

        return AgentOutput(
            agent_name=self.name,
            summary=f"Cleaned draft Markdown for review. Final Clean: {final_clean_report.audit_status} ({len(final_clean_report.findings)} findings).",
        )
