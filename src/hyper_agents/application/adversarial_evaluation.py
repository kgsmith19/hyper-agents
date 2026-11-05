"""Pinned, offline adversarial probe evaluation (P07-2)."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast


class ProbeError(ValueError):
    pass


@dataclass(frozen=True)
class Probe:
    id: str
    expected: str


@dataclass(frozen=True)
class ProbeSuite:
    probes: tuple[Probe, ...]


@dataclass(frozen=True)
class ProbeResult:
    id: str
    passed: bool


@dataclass(frozen=True)
class ProbeEvidence:
    results: tuple[ProbeResult, ...]
    passed: bool


_IDS = ("malformed-result", "over-budget", "policy-smuggling", "stale-run")


def load_probes(path: Path, pin: Path) -> ProbeSuite:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != pin.read_text().strip():
        raise ProbeError("probe digest mismatch")
    try:
        raw: object = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ProbeError("probe corpus is invalid") from error
    if not isinstance(raw, dict):
        raise ProbeError("probe corpus is invalid")
    data = cast(dict[str, object], raw)
    probes = data.get("probes")
    if data.get("version") != 1 or not isinstance(probes, list):
        raise ProbeError("probe corpus is invalid")
    loaded: list[Probe] = []
    for raw_probe in cast(list[object], probes):
        if not isinstance(raw_probe, dict):
            raise ProbeError("probe shape is invalid")
        probe = cast(dict[str, object], raw_probe)
        id_, expected = probe.get("id"), probe.get("expected")
        if (
            set(probe) != {"id", "expected"}
            or not isinstance(id_, str)
            or not isinstance(expected, str)
        ):
            raise ProbeError("probe shape is invalid")
        loaded.append(Probe(id_, expected))
    if tuple(sorted(p.id for p in loaded)) != _IDS or len(loaded) != 4:
        raise ProbeError("probe IDs are invalid")
    return ProbeSuite(tuple(sorted(loaded, key=lambda probe: probe.id)))


def evaluate_probes(suite: ProbeSuite, outcomes: dict[str, str]) -> ProbeEvidence:
    if set(outcomes) != {probe.id for probe in suite.probes}:
        raise ProbeError("probe outcomes are incomplete")
    results = tuple(
        ProbeResult(probe.id, outcomes[probe.id] == probe.expected) for probe in suite.probes
    )
    return ProbeEvidence(results, all(result.passed for result in results))
