"""Frozen P07 fixtures admit only pinned, equal-budget trusted-oracle evaluations."""

import hashlib
import json
from pathlib import Path

import pytest

from hyper_agents.application.evaluation import EvaluationError, VariantResult, evaluate, load_suite

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "eval" / "held_out" / "v1.json"
DIGEST = ROOT / "eval" / "held_out" / "v1.sha256"


def test_committed_held_out_suite_is_pinned_and_has_four_equal_budget_variants() -> None:
    suite = load_suite(FIXTURE, DIGEST)

    assert [case.variant for case in suite.cases] == ["baseline", "review", "recovery", "strict"]
    assert {case.budget for case in suite.cases} == {100}


def test_digest_drift_is_refused_before_a_fixture_is_used(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(FIXTURE.read_text())
    digest = tmp_path / "fixture.sha256"
    digest.write_text("0" * 64 + "\n")

    with pytest.raises(EvaluationError, match="digest"):
        load_suite(fixture, digest)


def test_suite_refuses_a_missing_variant_or_unequal_budget(tmp_path: Path) -> None:
    raw = json.loads(FIXTURE.read_text())
    raw["cases"] = raw["cases"][:-1]
    raw["cases"][0]["budget"] = 99
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(raw))
    digest = tmp_path / "fixture.sha256"
    digest.write_text(hashlib.sha256(fixture.read_bytes()).hexdigest() + "\n")

    with pytest.raises(EvaluationError, match="variants"):
        load_suite(fixture, digest)


def test_trusted_oracles_fail_wrong_outcomes_and_over_budget_results() -> None:
    suite = load_suite(FIXTURE, DIGEST)
    results = {
        "baseline": VariantResult(status="finished", state="done", tokens_used=100),
        "review": VariantResult(status="failed", state="done", tokens_used=50),
        "recovery": VariantResult(status="finished", state="done", tokens_used=101),
        "strict": VariantResult(status="finished", state="done", tokens_used=80),
    }

    evidence = evaluate(suite, results)

    assert evidence.passed is False
    assert {item.variant: item.passed for item in evidence.results} == {
        "baseline": True,
        "review": False,
        "recovery": False,
        "strict": True,
    }
