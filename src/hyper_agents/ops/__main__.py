"""CLI: python -m hyper_agents.ops run --op ID --params JSON --mode dry-run|apply [--gate ENV]

Subcommands: run (dry-run or apply), tier (print the op tier and gate for routing),
maintain (apply the R0/R1 repository-maintenance ops to every repository in the fleet; any
scheduler can run it).
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

from hyper_agents.ops.adapters import CliAdapter, GithubAdapter, InfisicalAdapter, VendorAdapter
from hyper_agents.ops.core import OpRefused, run_op
from hyper_agents.ops.fleet import RepoAdapter, load_fleet
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
    run.add_argument("--fleet", type=Path, default=Path("ops/fleet.yaml"))
    maintain = commands.add_parser("maintain")
    maintain.add_argument("--registry", type=Path, default=Path("ops/registry.yaml"))
    maintain.add_argument("--fleet", type=Path, default=Path("ops/fleet.yaml"))
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

    if args.command == "maintain":
        return _maintain(args.registry, args.fleet)

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
    adapters = {
        "infisical": infisical,
        "github": GithubAdapter(infisical),
        "vendor": VendorAdapter(infisical),
        "cli": CliAdapter(),
        "repo": RepoAdapter(load_fleet(args.fleet), infisical),
    }
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


MAINTAIN_OPS = ("repo.close_sweep", "repo.change_health", "repo.settings_drift")


def _maintain(registry_path: Path, fleet_path: Path) -> int:
    """Run the R0/R1 maintenance ops for every fleet repository; never an R2 op."""
    registry = load_registry(registry_path)
    fleet = load_fleet(fleet_path)
    infisical = InfisicalAdapter(project_id=os.environ.get("INFISICAL_PROJECT_ID", ""))
    adapters = {"repo": RepoAdapter(fleet, infisical)}
    failed = False
    for repo in fleet.repos:
        for op_id in MAINTAIN_OPS:
            if registry[op_id].tier not in ("R0", "R1"):
                raise SystemExit(f"{op_id} is not R0/R1; maintain never runs gated ops")
            receipt = run_op(
                registry,
                op_id,
                {"repo": repo},
                mode="apply",
                gate=None,
                adapters=adapters,
                actor=os.environ.get("GITHUB_ACTOR", "local"),
                run_id=os.environ.get("GITHUB_RUN_ID", uuid.uuid4().hex),
            )
            failed = failed or receipt["status"] == "failed"
            print(json.dumps(receipt, sort_keys=True))
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
