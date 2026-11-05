from pathlib import Path

import pytest

from hyper_agents.application.adversarial_evaluation import ProbeError, evaluate_probes, load_probes

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "eval" / "adversarial" / "v1.json"
DIGEST = ROOT / "eval" / "adversarial" / "v1.sha256"


def test_frozen_adversarial_corpus_has_the_four_expected_probes() -> None:
    suite = load_probes(CORPUS, DIGEST)
    assert [probe.id for probe in suite.probes] == [
        "malformed-result",
        "over-budget",
        "policy-smuggling",
        "stale-run",
    ]


def test_corrupted_probe_digest_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "v1.json"
    copy.write_bytes(CORPUS.read_bytes())
    pin = tmp_path / "v1.sha256"
    pin.write_text("0" * 64)
    with pytest.raises(ProbeError, match="digest"):
        load_probes(copy, pin)


def test_probe_oracle_rejects_a_mismatched_refusal() -> None:
    suite = load_probes(CORPUS, DIGEST)
    outcomes = {probe.id: probe.expected for probe in suite.probes}
    outcomes["stale-run"] = "policy_denied"
    evidence = evaluate_probes(suite, outcomes)
    assert evidence.passed is False
    assert evidence.results[3].passed is False
