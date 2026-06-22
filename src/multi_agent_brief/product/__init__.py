"""Product-layer report contracts and registries."""

from multi_agent_brief.product.policy_profile import PolicyProfile, validate_policy_profile_payload
from multi_agent_brief.product.policy_registry import PolicyProfileRegistry
from multi_agent_brief.product.report_pack import ReportPack, validate_report_pack_payload
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import (
    ReportSpecValidationResult,
    load_report_spec,
    validate_report_spec_payload,
)

__all__ = [
    "PolicyProfile",
    "PolicyProfileRegistry",
    "ReportPack",
    "ReportPackRegistry",
    "ReportSpecValidationResult",
    "load_report_spec",
    "validate_policy_profile_payload",
    "validate_report_pack_payload",
    "validate_report_spec_payload",
]
