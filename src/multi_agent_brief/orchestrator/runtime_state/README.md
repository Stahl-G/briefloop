# Runtime State Package Boundaries

This package implements the runtime control plane behind the stable public
facade `multi_agent_brief.orchestrator.runtime_state`.

The facade exports only the pinned public CLI/runtime functions and selected
schema constants needed by in-repo consumers. Internal modules may change, but
the control-plane record shapes below are the v1.0 freeze targets:

- `manifest.py`: `RUNTIME_MANIFEST_SCHEMA`
- `workflow.py`: `WORKFLOW_STATE_SCHEMA` and stage status values
- `artifact_registry.py`: `ARTIFACT_REGISTRY_SCHEMA` and artifact status values
- `event_log.py`: `EVENT_LOG_SCHEMA`, event type vocabulary, and actor vocabulary
- `operations.py`: `FACT_LAYER_IMPORT_SCHEMA` and fact-layer import transaction
  metadata shape

Layering:

- `_io.py`, `identity.py`, `paths.py`, and `contracts_loader.py` are low-level
  utilities.
- `workflow.py`, `manifest.py`, `artifact_registry.py`, and `event_log.py`
  own schema-bearing control records and deterministic projections.
- `completion_gates.py` owns completion and finalization reason construction.
- `operations.py` owns runtime verbs and transactions.

Do not reintroduce a read-through facade or private implementation proxy. New
public exports must be explicit in `__init__.py` and covered by public-surface
tests.
