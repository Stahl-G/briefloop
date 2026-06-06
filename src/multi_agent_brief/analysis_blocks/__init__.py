"""Analysis Block Contract — epistemic presentation layer."""
from multi_agent_brief.analysis_blocks.schemas import AnalysisBlock, CaseApplicability
from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
from multi_agent_brief.analysis_blocks.renderer import render_analysis_blocks

__all__ = [
    "AnalysisBlock",
    "CaseApplicability",
    "build_analysis_blocks",
    "render_analysis_blocks",
]
