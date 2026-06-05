#!/usr/bin/env python3
"""CI Gate: verify every user-facing provider has a CapabilitySpec registered."""
from __future__ import annotations

import sys

from multi_agent_brief.capabilities.catalog import CAPABILITIES
from multi_agent_brief.sources.registry import PROVIDER_CLASSES


def check_capability_registry() -> list[str]:
    """Check that every user-facing provider is registered as a capability.

    Returns a list of error messages (empty = all good).
    """
    errors: list[str] = []

    skip_providers = {"cached_package"}
    provider_to_cap = {cap.provider_name: cap.id for cap in CAPABILITIES}

    for provider_name in PROVIDER_CLASSES:
        if provider_name in skip_providers:
            continue
        if provider_name not in provider_to_cap:
            errors.append(
                f"Provider '{provider_name}' is not registered as a capability. "
                f"Add a CapabilitySpec in catalog.py with provider_name='{provider_name}'."
            )

    cap_ids = [cap.id for cap in CAPABILITIES]
    seen = set()
    for cid in cap_ids:
        if cid in seen:
            errors.append(f"Duplicate capability ID: '{cid}'")
        seen.add(cid)

    return errors


def main() -> int:
    errors = check_capability_registry()
    if errors:
        print("[FAIL] Capability registry check failed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"[OK] All {len(CAPABILITIES)} capabilities registered, {len(PROVIDER_CLASSES)} providers covered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
