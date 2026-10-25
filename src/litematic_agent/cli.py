"""CLI emits compact JSON to stdout and meaningful exit codes."""

import argparse
import json
from pathlib import Path
import sys

from .operations import dispatch

MAX_RESPONSE = 24_000


def compact(value):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_RESPONSE:
        # Keep write outcomes visible even if pathological names/states are huge.
        outcome = {k: v for k, v in value.items() if k in
                   ("applied", "output", "source_sha256", "output_sha256", "changed_assignments", "validation")}
        outcome.update(response_limited=True, message="Response exceeds 24000 bytes. Narrow box or lower limit.")
        return json.dumps(outcome, ensure_ascii=False, separators=(",", ":"))
    return text


def parser():
    p = argparse.ArgumentParser(description="Inspect and safely edit Litematica files with bounded JSON output")
    commands = p.add_subparsers(dest="action", required=True)
    for action in ("summary", "query", "inspect", "slice", "analyze", "edit", "validate"):
        cmd = commands.add_parser(action)
        cmd.add_argument("path", help="Input .litematic path")
        if action in ("query", "inspect", "slice", "analyze"):
            cmd.add_argument("--region")
        if action in ("query", "slice", "analyze"):
            cmd.add_argument("--box", type=int, nargs=6, metavar=("X1", "Y1", "Z1", "X2", "Y2", "Z2"))
        if action in ("summary", "query", "analyze"):
            cmd.add_argument("--limit", type=int, default=32 if action == "query" else 16)
        if action in ("query", "analyze"):
            cmd.add_argument("--max-scan", type=int, default=200_000)
        if action == "summary":
            cmd.add_argument("--region-offset", type=int, default=0)
        if action == "query":
            cmd.add_argument("--match")
            cmd.add_argument("--category", choices=("all", "redstone"), default="all")
            cmd.add_argument("--cursor", type=int, default=0)
        if action == "inspect":
            cmd.add_argument("--pos", type=int, nargs=3, required=True)
        if action == "slice":
            cmd.add_argument("--axis", choices=("x", "y", "z"), required=True)
            cmd.add_argument("--at", type=int, required=True)
        if action == "edit":
            cmd.add_argument("--plan", required=True, help="JSON plan file; defaults to dry-run")
            cmd.add_argument("--output", help="New .litematic file; existing files are never overwritten")
            cmd.add_argument("--apply", action="store_true", help="Write after validation; requires SHA256 in plan")
    return p


def main(argv=None):
    args = vars(parser().parse_args(argv))
    action, path = args.pop("action"), args.pop("path")
    try:
        if action == "edit":
            plan_path = Path(args["plan"])
            if plan_path.stat().st_size > 256_000:
                raise ValueError("Plan file exceeds 256000 bytes")
            args["plan"] = json.loads(plan_path.read_text(encoding="utf-8"))
            if args.get("output") and Path(args["output"]).suffix.lower() != ".litematic":
                raise ValueError("Output must have .litematic extension")
        print(compact(dispatch(action, path, **args)))
        return 0
    except (ValueError, OSError, TypeError, KeyError, OverflowError, EOFError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    sys.exit(main())
