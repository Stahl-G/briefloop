"""Product-layer report contracts and registries."""

from multi_agent_brief.product.report_pack import ReportPack, validate_report_pack_payload
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import (
    ReportSpecValidationResult,
    load_report_spec,
    validate_report_spec_payload,
)

__all__ = [
    "ReportPack",
    "ReportPackRegistry",
    "ReportSpecValidationResult",
    "load_report_spec",
    "validate_report_pack_payload",
    "validate_report_spec_payload",
]
