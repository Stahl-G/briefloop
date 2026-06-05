"""CI smoke test: run the pipeline and verify output."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    from multi_agent_brief.core.config import build_run_settings, load_config
    from multi_agent_brief.core.pipeline import BriefPipeline
    from multi_agent_brief.core.schemas import PipelineContext

    # Parse args
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    input_dir = sys.argv[2] if len(sys.argv) > 2 else "examples/basic_market_brief"
    output_dir = sys.argv[3] if len(sys.argv) > 3 else None
    ledger_path = sys.argv[4] if len(sys.argv) > 4 else None

    if config_path:
        config = load_config(config_path)
    else:
        config = None

    settings = build_run_settings(
        config=config,
        input_dir=input_dir,
        output_dir=output_dir,
        name=None,
        language=None,
        audience=None,
    )
    context = PipelineContext(**settings)
    BriefPipeline().run(context)

    # Verify claim_ledger if path given
    if ledger_path:
        d = json.load(open(ledger_path))
        print(f"claim_ledger has {len(d)} entries")
    else:
        # Try default location
        for candidate in [
            Path(output_dir or "") / "intermediate" / "claim_ledger.json",
            Path(output_dir or "") / "claim_ledger.json",
        ]:
            if candidate.exists():
                d = json.loads(candidate.read_text())
                print(f"claim_ledger has {len(d)} entries")
                break

    print("SMOKE PASSED")


if __name__ == "__main__":
    main()
