from __future__ import annotations

import argparse
from pathlib import Path

from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="multi-agent-brief")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the local MVP brief pipeline.")
    run_parser.add_argument("input_dir", help="Directory containing .md, .txt, or .json input files.")
    run_parser.add_argument("--output", default="output/demo", help="Output directory.")
    run_parser.add_argument("--name", default="Weekly Intelligence Brief", help="Brief title.")
    run_parser.add_argument("--language", default="en-US")
    run_parser.add_argument("--audience", default="management")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        context = PipelineContext(
            project_name=args.name,
            input_dir=str(Path(args.input_dir)),
            output_dir=str(Path(args.output)),
            language=args.language,
            audience=args.audience,
        )
        outputs = BriefPipeline().run(context)
        for output in outputs:
            print(f"[{output.agent_name}] {output.summary}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

