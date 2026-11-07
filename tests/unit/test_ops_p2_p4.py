"""Ops broker P2-P4 (agent-extensions#100): vendor key validation, GitHub variables and
settings, the CLI allowlist, receipt redaction gaps, CLI exit codes and the R2 gate wiring.

Every test is a negative control or pins a value-free receipt: no secret value may reach
a receipt, an error, argv, or a URL taken from parameters.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from hyper_agents.ops import __main__ as cli_main
from hyper_agents.ops.adapters import (
    CLI_ALLOWLIST,
    SETTINGS_JQ,
    VENDOR_ENDPOINTS,
    CliAdapter,
    GithubAdapter,
    VendorAdapter,
)
from hyper_agents.ops.core import OpRefused, fingerprint, run_op
from hyper_agents.ops.registry import ADAPTERS, load_registry
from tests.unit.test_ops import PLANTED, FakeInfisical, fake_runner

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "ops" / "registry.yaml")
KEY = {"provider": "openai", "source_path": "/dev/", "source_name": "DEV_OPENAI_API_KEY"}


def apply(op: str, params: dict[str, Any], adapters: dict[str, Any]) -> dict[str, Any]:
    return run_op(
        REGISTRY, op, params, mode="apply", gate=None, adapters=adapters, actor="t", run_id="r"
    )


def test_every_registered_op_names_a_known_adapter() -> None:
    assert {op.adapter for op in REGISTRY.values()} <= set(ADAPTERS)
    for op_id in (
        "vendor.key.validate",
        "github.variable.list",
        "github.variable.delete",
        "github.settings.get",
        "cli.run",
    ):
        assert op_id in REGISTRY


@pytest.mark.parametrize(("status", "valid"), [(200, True), (401, False), (429, False)])
def test_vendor_validation_reports_status_and_fingerprint_only(status: int, valid: bool) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def http(url: str, headers: dict[str, str]) -> int:
        seen.append((url, headers))
        return status

    receipt = apply("vendor.key.validate", KEY, {"vendor": VendorAdapter(FakeInfisical(), http)})
    assert receipt["status"] == "applied"
    assert receipt["result"] == {
        "provider": "openai",
        "valid": valid,
        "http_status": status,
        "fingerprint": fingerprint(PLANTED),
    }
    assert PLANTED not in json.dumps(receipt)
    assert seen[0][0] == VENDOR_ENDPOINTS["openai"][0]
    assert PLANTED in seen[0][1]["Authorization"]  # the key goes in a header, never the URL
    assert PLANTED not in seen[0][0]


def test_vendor_transport_error_is_redacted() -> None:
    def http(url: str, headers: dict[str, str]) -> int:
        raise OSError(f"connection reset while sending {headers}")

    receipt = apply("vendor.key.validate", KEY, {"vendor": VendorAdapter(FakeInfisical(), http)})
    assert receipt["status"] == "failed"
    assert PLANTED not in json.dumps(receipt)


@pytest.mark.parametrize(
    "params",
    [
        {**KEY, "provider": "evil"},
        {**KEY, "url": "https://attacker.example"},
        {**KEY, "source_name": "lower"},
    ],
)
def test_vendor_params_outside_schema_are_refused_before_any_adapter(
    params: dict[str, str],
) -> None:
    def explode(url: str, headers: dict[str, str]) -> int:
        raise AssertionError("adapter must not run")

    with pytest.raises(OpRefused):
        apply("vendor.key.validate", params, {"vendor": VendorAdapter(FakeInfisical(), explode)})


def test_variable_list_delete_and_settings_use_fixed_argv() -> None:
    calls, run = fake_runner()
    github = GithubAdapter(FakeInfisical(), run=run)
    repo = {"repo": "kgsmith19/hyper-agents"}
    apply("github.variable.list", repo, {"github": github})
    apply("github.variable.delete", {**repo, "name": "OLD_FLAG"}, {"github": github})
    calls_before = len(calls)
    receipt = apply("github.settings.get", repo, {"github": github})
    assert calls[0][0] == ["gh", "variable", "list", "-R", repo["repo"], "--json", "name"]
    assert calls[1][0] == ["gh", "variable", "delete", "OLD_FLAG", "-R", repo["repo"]]
    assert calls[calls_before][0] == ["gh", "api", f"repos/{repo['repo']}", "--jq", SETTINGS_JQ]
    assert receipt["status"] in ("applied", "failed")
    assert all(PLANTED not in " ".join(argv) for argv, _ in calls)


def test_settings_get_is_read_only_tier() -> None:
    assert REGISTRY["github.settings.get"].tier == "R0"
    assert REGISTRY["github.variable.delete"].tier == "R1"


def test_cli_runs_only_allowlisted_argv() -> None:
    calls, run = fake_runner()
    receipt = apply("cli.run", {"command": "gh-version"}, {"cli": CliAdapter(run=run)})
    assert receipt["status"] == "applied"
    assert calls == [(CLI_ALLOWLIST["gh-version"], None)]


@pytest.mark.parametrize("command", ["rm -rf /", "gh-version; id", "bash"])
def test_cli_refuses_anything_not_allowlisted(command: str) -> None:
    calls, run = fake_runner()
    with pytest.raises(OpRefused):
        apply("cli.run", {"command": command}, {"cli": CliAdapter(run=run)})
    assert calls == []
    with pytest.raises(ValueError, match="not allowlisted"):
        CliAdapter(run=run).execute("run", {"command": command})


def test_registry_cli_enum_matches_the_code_allowlist() -> None:
    (param,) = REGISTRY["cli.run"].params
    assert set(param.enum or ()) == set(CLI_ALLOWLIST)


def test_exists_and_delete_receipts_carry_no_values() -> None:
    _calls, run = fake_runner()
    infisical = FakeInfisical()
    exists = run_op(
        REGISTRY,
        "infisical.secret.exists",
        {"source_path": "/dev/", "source_name": "DEV_OPENAI_API_KEY"},
        mode="apply",
        gate=None,
        adapters={"infisical": _Exists(infisical)},
        actor="t",
        run_id="r",
    )
    deleted = run_op(
        REGISTRY,
        "github.secret.delete",
        {"repo": "kgsmith19/hyper-agents", "name": "OLD_KEY"},
        mode="apply",
        gate="ops-r2",
        adapters={"github": GithubAdapter(infisical, run=run)},
        actor="t",
        run_id="r",
    )
    for receipt in (exists, deleted):
        assert PLANTED not in json.dumps(receipt)
    assert exists["result"]["fingerprint"] == fingerprint(PLANTED)


class _Exists:
    def __init__(self, infisical: FakeInfisical) -> None:
        self._infisical = infisical

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        value = self._infisical.get(params["source_path"], params["source_name"])
        return {"exists": True, "fingerprint": fingerprint(value)}


def test_failed_variable_set_redacts_the_value_from_the_error() -> None:
    """Variable values are non-secret config and appear in params by design; a failure's
    error text must still never echo them (the stdin value is redacted)."""
    _calls, run = fake_runner(returncode=1, stderr=f"HTTP 422 value {PLANTED} rejected")
    receipt = apply(
        "github.variable.set",
        {"repo": "kgsmith19/hyper-agents", "name": "SOME_FLAG", "value": PLANTED},
        {"github": GithubAdapter(FakeInfisical(), run=run)},
    )
    assert receipt["status"] == "failed"
    assert PLANTED not in receipt["error"]
    assert "[REDACTED]" in receipt["error"]


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["run", "--op", "nope.op"], 2),
        (["run", "--op", "cli.run", "--params", "{not json"], 2),
        (["run", "--op", "cli.run", "--params", "[]"], 2),
        (["run", "--op", "cli.run", "--params", '{"command": "gh-version"}'], 0),
        (["tier", "--op", "nope.op"], 2),
        (["tier", "--op", "github.secret.set"], 0),
    ],
)
def test_cli_exit_codes(argv: list[str], code: int, capsys: pytest.CaptureFixture[str]) -> None:
    registry = ["--registry", str(ROOT / "ops" / "registry.yaml")]
    assert cli_main.main([*argv, *registry]) == code
    out = json.loads(capsys.readouterr().out)
    if code == 2:
        assert out["status"] == "refused"


def test_ops_workflow_gates_r2_on_the_owner_environment_and_refuses_r3() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ops.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["apply-r2"]["environment"] == "ops-r2"
    assert "R2" in jobs["apply-r2"]["if"] and "R2" in jobs["apply-r0-r1"]["if"]
    assert "environment" not in jobs["apply-r0-r1"]
    plan = json.dumps(jobs["plan"])
    assert "R3 ops are not wired yet" in plan


def test_deleting_an_already_absent_secret_succeeds_idempotently() -> None:
    # Negative control first: a real 404 from `gh secret delete` (the secret is absent) must
    # not fail the run; any other failure must still fail it.
    calls, run = fake_runner(
        returncode=1,
        stderr="failed to delete secret OPS_GATE_PROOF_ABSENT: HTTP 404 (https://api.github.com/repos/x/actions/secrets/OPS_GATE_PROOF_ABSENT)",
    )
    github = GithubAdapter(FakeInfisical(), run=run)
    receipt = run_op(
        REGISTRY,
        "github.secret.delete",
        {"repo": "kgsmith19/hyper-agents", "name": "OPS_GATE_PROOF_ABSENT"},
        mode="apply",
        gate="ops-r2",
        adapters={"github": github},
        actor="t",
        run_id="r",
    )
    assert receipt["status"] == "applied"
    assert calls[0][0] == [
        "gh",
        "secret",
        "delete",
        "OPS_GATE_PROOF_ABSENT",
        "-R",
        "kgsmith19/hyper-agents",
    ]


def test_deleting_a_secret_still_fails_on_other_errors() -> None:
    _, run = fake_runner(returncode=1, stderr="HTTP 403: Resource not accessible by integration")
    github = GithubAdapter(FakeInfisical(), run=run)
    receipt = run_op(
        REGISTRY,
        "github.secret.delete",
        {"repo": "kgsmith19/hyper-agents", "name": "OPS_GATE_PROOF_ABSENT"},
        mode="apply",
        gate="ops-r2",
        adapters={"github": github},
        actor="t",
        run_id="r",
    )
    assert receipt["status"] == "failed"
