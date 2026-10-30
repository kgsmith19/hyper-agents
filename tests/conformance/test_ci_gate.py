"""The PR Gate rollup and the license audit must be able to fail (negative controls)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

CI = Path(__file__).resolve().parents[2] / "ci"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"ci_{name}", CI / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = load("gate")
licenses = load("licenses")
ALL_GREEN = {lane: {"result": "success"} for lane in gate.MANDATED}


def test_the_gate_passes_only_when_every_lane_succeeded() -> None:
    assert gate.verdict(ALL_GREEN)[0] is True


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", "neutral", ""])
def test_any_non_success_lane_fails_the_gate(result: str) -> None:
    for lane in gate.MANDATED:
        needs = {**ALL_GREEN, lane: {"result": result}}
        assert gate.verdict(needs)[0] is False


def test_a_missing_lane_fails_the_gate() -> None:
    needs = dict(ALL_GREEN)
    del needs["tests"]
    ok, lines = gate.verdict(needs)
    assert ok is False
    assert "FAIL tests: missing" in lines


def test_license_audit_rejects_copyleft_and_unknown() -> None:
    rows = [
        {"Name": "ok", "License": "MIT License"},
        {"Name": "bad", "License": "GNU General Public License v3 (GPLv3)"},
        {"Name": "mystery", "License": "UNKNOWN"},
        {"Name": "hyper-agents", "License": "UNKNOWN"},
    ]
    assert licenses.problems(rows) == [
        "bad: GNU General Public License v3 (GPLv3)",
        "mystery: license unknown",
    ]


def test_the_workflow_gate_needs_exactly_the_mandated_lanes() -> None:
    import yaml

    workflow = yaml.safe_load((CI.parent / ".github/workflows/pr-gate.yml").read_text())
    gate_job = workflow["jobs"]["pr-gate"]
    assert gate_job["name"] == "PR Gate"
    assert gate_job["if"] == "always()"
    assert sorted(gate_job["needs"]) == sorted(gate.MANDATED)
    assert set(workflow["jobs"]) == {*gate.MANDATED, "pr-gate"}


def test_issue_manifest_is_in_dependency_order_and_complete() -> None:
    import json

    manifest = json.loads((CI.parent / "docs/issues/manifest.json").read_text())
    seen: set[str] = set()
    for issue in manifest["issues"]:
        assert set(issue["depends_on"]) <= seen, f"{issue['key']} depends on a later issue"
        for field in (
            "files",
            "red_test",
            "acceptance",
            "negative_cases",
            "inspiration",
            "rollback",
        ):
            assert issue[field], f"{issue['key']} has no {field}"
        assert len(issue["acceptance"]) <= 5
        seen.add(issue["key"])
