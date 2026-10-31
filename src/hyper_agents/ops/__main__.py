"""CLI: python -m hyper_agents.ops run --op ID --params JSON --mode dry-run|apply [--gate ENV]

Subcommands: run (dry-run or apply), tier (print the op tier and gate for routing).
Exit codes: 0 planned or applied, 1 apply failed, 2 refused before any adapter ran.
Prints the receipt as JSON. Never prints secret values.
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, cast

from hyper_agents.ops.adapters import GithubAdapter, InfisicalAdapter
from hyper_agents.ops.core import OpRefused, run_op
from hyper_agents.ops.registry import load_registry

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hyper_agents.ops")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--op", required=True)
    run.add_argument("--params", default="{}")
    run.add_argument("--mode", default="dry-run", choices=("dry-run", "apply"))
    run.add_argument("--gate", default=None)
    run.add_argument("--registry", type=Path, default=Path("ops/registry.yaml"))
    tier = commands.add_parser("tier")
    tier.add_argument("--op", required=True)
    tier.add_argument("--registry", type=Path, default=Path("ops/registry.yaml"))
    args = parser.parse_args(argv)

    if args.command == "tier":
        op = load_registry(args.registry).get(args.op)
        if op is None:
            print(json.dumps({"status": "refused", "error": f"unregistered op: {args.op!r}"}))
            return EXIT_REFUSED
        print(json.dumps({"op": op.id, "tier": op.tier, "gate": op.gate or ""}))
        return EXIT_OK

    try:
        decoded: Any = json.loads(args.params)
    except json.JSONDecodeError:
        print(json.dumps({"status": "refused", "error": "params is not valid JSON"}))
        return EXIT_REFUSED
    if not isinstance(decoded, dict):
        print(json.dumps({"status": "refused", "error": "params must be a JSON object"}))
        return EXIT_REFUSED
    raw_params = cast(dict[str, Any], decoded)

    infisical = InfisicalAdapter(project_id=os.environ.get("INFISICAL_PROJECT_ID", ""))
    adapters = {"infisical": infisical, "github": GithubAdapter(infisical)}
    try:
        receipt = run_op(
            load_registry(args.registry),
            args.op,
            raw_params,
            mode=args.mode,
            gate=args.gate,
            adapters=adapters,
            actor=os.environ.get("GITHUB_ACTOR", "local"),
            run_id=os.environ.get("GITHUB_RUN_ID", uuid.uuid4().hex),
        )
    except OpRefused as error:
        print(json.dumps({"status": "refused", "op": args.op, "error": str(error)}))
        return EXIT_REFUSED

    print(json.dumps(receipt, indent=2, sort_keys=True))
    return EXIT_FAILED if receipt["status"] == "failed" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
