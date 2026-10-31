"""Ops broker negative controls: refusals precede adapters, and no secret value leaks."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hyper_agents.ops.adapters import GithubAdapter, InfisicalAdapter
from hyper_agents.ops.core import OpRefused, run_op
from hyper_agents.ops.registry import RegistryError, load_registry

ROOT = Path(__file__).resolve().parents[2]
PLANTED = "PLANTED-SECRET-VALUE-0123456789"


class SpyAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        self.calls.append((action, dict(params)))
        return {"ok": True}


class FakeInfisical(InfisicalAdapter):
    def __init__(self) -> None:
        super().__init__(project_id="test-project")

    def get(self, path: str, name: str) -> str:
        return PLANTED


def fake_runner(
    returncode: int = 0, stderr: str = ""
) -> tuple[list[tuple[list[str], str | None]], Any]:
    calls: list[tuple[list[str], str | None]] = []

    def run(argv: list[str], stdin: str | None, env: dict[str, str]) -> tuple[int, str, str]:
        calls.append((argv, stdin))
        return returncode, "[]" if "list" in argv else "", stderr

    return calls, run


@pytest.fixture
def registry() -> dict[str, Any]:
    return load_registry(ROOT / "ops" / "registry.yaml")


def _run(
    registry: dict[str, Any],
    op: str,
    params: dict[str, Any],
    *,
    mode: str = "dry-run",
    gate: str | None = None,
    adapter: SpyAdapter | None = None,
) -> dict[str, Any]:
    spy = adapter or SpyAdapter()
    return run_op(
        registry,
        op,
        params,
        mode=mode,
        gate=gate,
        adapters={"github": spy, "infisical": spy},
        actor="test",
        run_id="run-1",
    )


SET_PARAMS = {
    "repo": "kgsmith19/hyper-agents",
    "name": "LLM_API_KEY",
    "source_path": "/dev/",
    "source_name": "DEV_OPENAI_API_KEY",
}


def test_unregistered_op_is_refused_before_any_adapter(registry: dict[str, Any]) -> None:
    spy = SpyAdapter()
    with pytest.raises(OpRefused, match="unregistered op"):
        _run(registry, "github.repo.delete", {}, mode="apply", adapter=spy)
    assert spy.calls == []


def test_unknown_param_is_refused(registry: dict[str, Any]) -> None:
    spy = SpyAdapter()
    with pytest.raises(OpRefused, match="unknown params"):
        _run(
            registry,
            "github.secret.list",
            {"repo": "kgsmith19/hyper-agents", "x": "1"},
            adapter=spy,
        )
    assert spy.calls == []


def test_pattern_violation_is_refused(registry: dict[str, Any]) -> None:
    spy = SpyAdapter()
    with pytest.raises(OpRefused, match="allowed pattern"):
        _run(registry, "github.secret.set", {**SET_PARAMS, "name": "lower_case"}, adapter=spy)
    assert spy.calls == []


def test_repo_outside_allowlist_is_refused(registry: dict[str, Any]) -> None:
    spy = SpyAdapter()
    with pytest.raises(OpRefused, match="not an allowed value"):
        _run(registry, "github.secret.list", {"repo": "someone/else"}, adapter=spy)
    assert spy.calls == []


def test_r2_apply_without_gate_is_refused(registry: dict[str, Any]) -> None:
    spy = SpyAdapter()
    with pytest.raises(OpRefused, match="apply requires gate"):
        _run(registry, "github.secret.set", SET_PARAMS, mode="apply", adapter=spy)
    assert spy.calls == []


def test_r2_apply_with_wrong_gate_is_refused(registry: dict[str, Any]) -> None:
    spy = SpyAdapter()
    with pytest.raises(OpRefused, match="apply requires gate"):
        _run(registry, "github.secret.set", SET_PARAMS, mode="apply", gate="ops-r0", adapter=spy)
    assert spy.calls == []


def test_dry_run_calls_no_adapter(registry: dict[str, Any]) -> None:
    spy = SpyAdapter()
    receipt = _run(registry, "github.secret.set", SET_PARAMS, mode="dry-run", adapter=spy)
    assert receipt["status"] == "planned"
    assert spy.calls == []


def test_r2_apply_with_matching_gate_runs(registry: dict[str, Any]) -> None:
    spy = SpyAdapter()
    receipt = _run(
        registry, "github.secret.set", SET_PARAMS, mode="apply", gate="ops-r2", adapter=spy
    )
    assert receipt["status"] == "applied"
    assert [action for action, _ in spy.calls] == ["set_secret"]


def test_secret_value_never_reaches_receipt_or_argv() -> None:
    calls, run = fake_runner()
    github = GithubAdapter(FakeInfisical(), run=run)
    result = github.execute("set_secret", SET_PARAMS)
    assert len(result["fingerprint"]) == 8
    argv, stdin = calls[-1]
    assert stdin == PLANTED
    assert PLANTED not in " ".join(argv)
    receipt = json.dumps({"result": result})
    assert PLANTED not in receipt


def test_failed_apply_redacts_secret_from_error() -> None:
    calls, run = fake_runner(returncode=1, stderr=f"rejected value {PLANTED}")
    github = GithubAdapter(FakeInfisical(), run=run)
    with pytest.raises(RuntimeError) as failure:
        github.execute("set_secret", SET_PARAMS)
    assert PLANTED not in str(failure.value)
    assert calls


def test_registry_loads_and_gates_r2_ops(registry: dict[str, Any]) -> None:
    assert {"github.secret.set", "github.secret.delete", "github.secret.list"} <= set(registry)
    assert registry["github.secret.set"].gate == "ops-r2"
    assert registry["github.secret.delete"].gate == "ops-r2"
    assert registry["github.secret.list"].tier == "R0"


def test_registry_rejects_gated_tier_without_gate(tmp_path: Path) -> None:
    bad = tmp_path / "registry.yaml"
    bad.write_text(
        "ops:\n  - id: x.y\n    adapter: github\n    action: set_secret\n    tier: R2\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="requires a gate"):
        load_registry(bad)


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    bad = tmp_path / "registry.yaml"
    block = "  - id: x.y\n    adapter: github\n    action: list_secrets\n    tier: R0\n"
    bad.write_text("ops:\n" + block + block, encoding="utf-8")
    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(bad)
