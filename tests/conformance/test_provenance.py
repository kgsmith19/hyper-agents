"""P07-3: release SBOM and provenance are deterministic and tamper-evident (negative controls)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
Artifacts = tuple[bytes, bytes, dict[str, str]]


def load_provenance() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_provenance", ROOT / "ci" / "provenance.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


provenance = load_provenance()


@pytest.fixture
def lock_text() -> str:
    return (ROOT / "uv.lock").read_text()


@pytest.fixture
def artifacts(lock_text: str) -> tuple[bytes, bytes, dict[str, str]]:
    sbom = provenance.build_sbom(lock_text)
    evidence = provenance.build_license_evidence()
    manifest = provenance.build_manifest(sbom, evidence, SHA)
    return sbom, evidence, manifest


def test_sbom_is_deterministic(lock_text: str) -> None:
    assert provenance.build_sbom(lock_text) == provenance.build_sbom(lock_text)


def test_sbom_lists_locked_packages_with_versions(lock_text: str) -> None:
    doc = json.loads(provenance.build_sbom(lock_text))
    assert doc["bomFormat"] == "CycloneDX"
    components = {c["name"]: c for c in doc["components"]}
    assert components["openhands-sdk"]["version"] == "1.58.2"
    assert components["openhands-sdk"]["licenses"] == [{"license": {"id": "MIT"}}]
    assert "hyper-agents" not in components
    names = [c["name"] for c in doc["components"]]
    assert names == sorted(names), "components must be sorted for determinism"


def test_sbom_contains_no_absolute_paths(artifacts: Artifacts) -> None:
    sbom, evidence, _ = artifacts
    for blob in (sbom, evidence):
        text = blob.decode()
        assert str(ROOT) not in text
        assert "/home/" not in text and "/root/" not in text


def test_manifest_binds_digests_and_sha(artifacts: Artifacts) -> None:
    sbom, evidence, manifest = artifacts
    assert manifest["source_sha"] == SHA
    assert manifest["sbom_sha256"] == provenance.sha256_hex(sbom)
    assert manifest["license_evidence_sha256"] == provenance.sha256_hex(evidence)
    provenance.verify(manifest, sbom, evidence)


def test_verify_rejects_changed_sbom(artifacts: Artifacts) -> None:
    sbom, evidence, manifest = artifacts
    altered = sbom.replace(b"1.58.2", b"1.58.3", 1)
    with pytest.raises(provenance.ProvenanceError, match="sbom"):
        provenance.verify(manifest, altered, evidence)


def test_verify_rejects_changed_license_evidence(artifacts: Artifacts) -> None:
    sbom, evidence, manifest = artifacts
    altered = evidence.replace(b"MIT", b"GPL", 1)
    with pytest.raises(provenance.ProvenanceError, match="license"):
        provenance.verify(manifest, sbom, altered)


@pytest.mark.parametrize("bad_sha", ["", "abc", "g" * 40, SHA.upper(), SHA + "0", "a" * 39])
def test_verify_rejects_malformed_sha(artifacts: Artifacts, bad_sha: str) -> None:
    sbom, evidence, manifest = artifacts
    manifest = {**manifest, "source_sha": bad_sha}
    with pytest.raises(provenance.ProvenanceError, match="source_sha"):
        provenance.verify(manifest, sbom, evidence)
