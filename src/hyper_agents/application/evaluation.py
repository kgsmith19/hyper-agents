"""Deterministic loader and trusted oracles for the frozen P07 evaluation corpus."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_VARIANTS = ("baseline", "review", "recovery", "strict")


class EvaluationError(ValueError):
    """A fixture or result cannot be trusted as an evaluation input."""


@dataclass(frozen=True)
class FixtureCase:
    variant: str
    budget: int
    expected_status: str
    expected_state: str | None


@dataclass(frozen=True)
class EvaluationSuite:
    cases: tuple[FixtureCase, ...]
    digest: str


@dataclass(frozen=True)
class VariantResult:
    status: str
    state: str | None
    tokens_used: int


@dataclass(frozen=True)
class OracleResult:
    variant: str
    passed: bool


@dataclass(frozen=True)
class EvaluationEvidence:
    results: tuple[OracleResult, ...]
    passed: bool


def load_suite(fixture_path: Path, digest_path: Path) -> EvaluationSuite:
    """Load a frozen corpus only when its committed SHA-256 pin is exact."""
    payload = fixture_path.read_bytes()
    expected_digest = digest_path.read_text(encoding="utf-8").strip()
    actual_digest = hashlib.sha256(payload).hexdigest()
    if expected_digest != actual_digest:
        raise EvaluationError("fixture digest mismatch")
    try:
        raw: object = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise EvaluationError("fixture is not valid JSON") from error
    if not isinstance(raw, dict):
        raise EvaluationError("fixture shape is invalid")
    fixture = cast(dict[str, object], raw)
    if set(fixture) != {"version", "cases"} or fixture["version"] != 1:
        raise EvaluationError("fixture shape is invalid")
    cases = fixture["cases"]
    if not isinstance(cases, list):
        raise EvaluationError("fixture cases are invalid")
    loaded = tuple(_load_case(case) for case in cast(list[object], cases))
    variants = tuple(case.variant for case in loaded)
    if set(variants) != set(_VARIANTS) or len(variants) != len(_VARIANTS):
        raise EvaluationError("fixture variants must be exactly the four trusted variants")
    if len({case.budget for case in loaded}) != 1:
        raise EvaluationError("fixture budgets must be equal")
    by_variant = {case.variant: case for case in loaded}
    return EvaluationSuite(tuple(by_variant[variant] for variant in _VARIANTS), actual_digest)


def evaluate(suite: EvaluationSuite, results: Mapping[str, VariantResult]) -> EvaluationEvidence:
    """Compare injected outcomes against the frozen trusted oracle without external calls."""
    expected_variants = {case.variant for case in suite.cases}
    if set(results) != expected_variants:
        raise EvaluationError("results must cover exactly the frozen variants")
    oracle_results = tuple(
        OracleResult(
            case.variant,
            _matches(case, results[case.variant]),
        )
        for case in suite.cases
    )
    return EvaluationEvidence(oracle_results, all(result.passed for result in oracle_results))


def _load_case(raw: object) -> FixtureCase:
    if not isinstance(raw, dict):
        raise EvaluationError("fixture case shape is invalid")
    case = cast(dict[str, object], raw)
    if set(case) != {"id", "variant", "budget", "expected"}:
        raise EvaluationError("fixture case shape is invalid")
    variant, budget, expected = case["variant"], case["budget"], case["expected"]
    if (
        not isinstance(variant, str)
        or not isinstance(budget, int)
        or isinstance(budget, bool)
        or budget <= 0
    ):
        raise EvaluationError("fixture case fields are invalid")
    if not isinstance(expected, dict):
        raise EvaluationError("fixture oracle is invalid")
    oracle = cast(dict[str, object], expected)
    if set(oracle) != {"status", "state"}:
        raise EvaluationError("fixture oracle is invalid")
    status, state = oracle["status"], oracle["state"]
    if not isinstance(status, str) or not isinstance(state, str | type(None)):
        raise EvaluationError("fixture oracle values are invalid")
    return FixtureCase(variant, budget, status, state)


def _matches(case: FixtureCase, result: VariantResult) -> bool:
    return (
        not isinstance(result.tokens_used, bool)
        and 0 <= result.tokens_used <= case.budget
        and result.status == case.expected_status
        and result.state == case.expected_state
    )
