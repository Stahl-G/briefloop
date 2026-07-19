"""Single adapter over the core-owned source-plan interpreter."""

from multi_agent_brief.contracts.v2 import RuntimeSourcePlanBinding
from multi_agent_brief.core_run_v2.service import _derive_runtime_source_plan


def derive_runtime_source_plan(
    content: bytes,
    *,
    run_id: str,
    sources_config_sha256: str,
) -> RuntimeSourcePlanBinding:
    return _derive_runtime_source_plan(
        content,
        run_id=run_id,
        sources_config_sha256=sources_config_sha256,
    )


__all__ = ["derive_runtime_source_plan"]
