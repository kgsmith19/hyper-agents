"""The license audit must accept only verified name==version provenance (negative controls)."""

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
PROVENANCED = (
    "openhands-sdk",
    "openhands-workspace",
    "openhands-agent-server",
    "agent-client-protocol",
)


def load_licenses() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_licenses", ROOT / "ci" / "licenses.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


licenses = load_licenses()


def unlicensed(name: str, version: str) -> dict[str, str]:
    return {"Name": name, "Version": version, "License": "UNKNOWN"}


def test_verified_pins_are_accepted() -> None:
    rows = [
        unlicensed(name, version)
        for name, version in (
            ("openhands-sdk", "1.58.2"),
            ("openhands-workspace", "1.58.2"),
            ("openhands-agent-server", "1.49.5"),
            ("agent-client-protocol", "0.12.1"),
        )
    ]
    assert licenses.problems(rows) == []


def test_unverified_newer_version_is_rejected() -> None:
    assert licenses.problems([unlicensed("openhands-sdk", "1.59.0")]) == [
        "openhands-sdk: license unknown"
    ]


def test_unverified_older_version_is_rejected() -> None:
    assert licenses.problems([unlicensed("openhands-sdk", "1.58.0")]) == [
        "openhands-sdk: license unknown"
    ]


def test_every_locked_provenanced_package_has_a_manifest_record() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {pkg["name"]: pkg["version"] for pkg in lock["package"]}
    for name in PROVENANCED:
        key = f"{name}=={locked[name]}"
        assert key in licenses.PROVENANCE_MANIFEST, f"uv.lock pins {key} with no provenance record"
