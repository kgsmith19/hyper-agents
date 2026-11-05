"""Release SBOM and provenance (P07-3). Offline and deterministic.

Builds three artifacts from the committed lock file and the license-audit record:

- sbom.cdx.json: CycloneDX 1.5 JSON listing every locked package (no timestamps, sorted).
- license-evidence.json: the verified license records from ci/licenses.py.
- provenance.json: binds the SBOM digest, the license-evidence digest, and the source commit SHA.

`verify` rejects an altered SBOM, altered license evidence, or a malformed SHA.
"""

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SELF = "hyper-agents"


class ProvenanceError(Exception):
    pass


def _licenses() -> Any:
    spec = importlib.util.spec_from_file_location("ci_licenses", ROOT / "ci" / "licenses.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


def build_license_evidence() -> bytes:
    """The verified license records the audit relies on (ci/licenses.py PROVENANCE_MANIFEST)."""
    records = _licenses().PROVENANCE_MANIFEST
    return _dump({"verified_licenses": records})


def build_sbom(lock_text: str) -> bytes:
    manifest = _licenses().PROVENANCE_MANIFEST
    lock = tomllib.loads(lock_text)
    components: list[dict[str, Any]] = []
    for pkg in lock["package"]:
        name, version = pkg["name"], pkg["version"]
        if name == SELF:
            continue
        record = manifest.get(f"{name}=={version}")
        licenses = [{"license": {"id": record["license"]}}] if record else []
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
                "licenses": licenses,
            }
        )
    components.sort(key=lambda c: (str(c["name"]), str(c["version"])))
    doc: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": SELF}},
        "components": components,
    }
    return _dump(doc)


def build_manifest(sbom: bytes, evidence: bytes, source_sha: str) -> dict[str, str]:
    if not SHA_RE.match(source_sha):
        raise ProvenanceError(f"source_sha is malformed: {source_sha!r}")
    return {
        "sbom_sha256": sha256_hex(sbom),
        "license_evidence_sha256": sha256_hex(evidence),
        "source_sha": source_sha,
    }


def verify(manifest: dict[str, str], sbom: bytes, evidence: bytes) -> None:
    sha = manifest.get("source_sha", "")
    if not SHA_RE.match(sha):
        raise ProvenanceError(f"source_sha is malformed: {sha!r}")
    if sha256_hex(sbom) != manifest.get("sbom_sha256"):
        raise ProvenanceError("sbom digest does not match the provenance manifest")
    if sha256_hex(evidence) != manifest.get("license_evidence_sha256"):
        raise ProvenanceError("license evidence digest does not match the provenance manifest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release SBOM and provenance (P07-3).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--source-sha", required=True)
    gen.add_argument("--out-dir", type=Path, required=True)
    ver = sub.add_parser("verify")
    ver.add_argument("--dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        sbom = build_sbom((ROOT / "uv.lock").read_text())
        evidence = build_license_evidence()
        manifest = build_manifest(sbom, evidence, args.source_sha)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "sbom.cdx.json").write_bytes(sbom)
        (args.out_dir / "license-evidence.json").write_bytes(evidence)
        (args.out_dir / "provenance.json").write_bytes(_dump(manifest))
        print("provenance: generated")
        return 0

    try:
        manifest = json.loads((args.dir / "provenance.json").read_text())
        verify(
            manifest,
            (args.dir / "sbom.cdx.json").read_bytes(),
            (args.dir / "license-evidence.json").read_bytes(),
        )
    except ProvenanceError as exc:
        print(f"provenance: {exc}")
        return 1
    print("provenance: verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
