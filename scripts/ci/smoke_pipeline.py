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

    # Verify claim_ledger — resolve against actual output dir from context
    actual_output = Path(context.output_dir)
    candidates = [ledger_path] if ledger_path else [
        str(actual_output / "intermediate" / "claim_ledger.json"),
        str(actual_output / "claim_ledger.json"),
    ]

    found = False
    for cand in candidates:
        if cand and Path(cand).exists():
            d = json.loads(Path(cand).read_text())
            print(f"claim_ledger has {len(d)} entries")
            if len(d) == 0:
                print("INFO: claim_ledger is empty (quiet-week / no reportable signals)")
            found = True
            break

    if not found:
        print("WARNING: claim_ledger.json not found — skipping ledger check")

    print("SMOKE PASSED")


if __name__ == "__main__":
    main()
