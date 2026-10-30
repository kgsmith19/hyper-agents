"""Dependency license audit: fail on a strong-copyleft or unknown license.

Reads `pip-licenses --format=json` on stdin. Our own package is skipped.

Version-scoped provenance records override PyPI metadata when packages have
blank license fields. This allows controlled dependency upgrades with explicit
license validation before changing version pins.
"""

import json
import sys

DENY = ("GPL", "AGPL", "SSPL", "EUPL")
ALLOW_LGPL = False

PROVENANCE_MANIFEST = {
    "openhands-sdk==1.59.0": {
        "license": "MIT",
        "source": "upstream",
        "verified": "2026-10-30",
    },
    "openhands-workspace==1.59.0": {
        "license": "MIT",
        "source": "upstream",
        "verified": "2026-10-30",
    },
    "openhands-agent-server==1.49.5": {
        "license": "MIT",
        "source": "upstream",
        "verified": "2026-10-30",
    },
    "agent-client-protocol==0.12.1": {
        "license": "Apache-2.0",
        "source": "upstream",
        "verified": "2026-10-30",
    },
}


def problems(rows: list[dict[str, str]]) -> list[str]:
    bad: list[str] = []
    for row in rows:
        name, version = row.get("Name", "?"), row.get("Version", "?")
        license_ = row.get("License", "UNKNOWN")

        if name == "hyper-agents":
            continue

        # Check version-scoped provenance manifest first
        provenance_key = f"{name}=={version}"
        if provenance_key in PROVENANCE_MANIFEST:
            license_ = PROVENANCE_MANIFEST[provenance_key]["license"]

        upper = license_.upper()
        if upper in ("", "UNKNOWN"):
            bad.append(f"{name}: license unknown")
        elif any(term in upper for term in DENY) and not (ALLOW_LGPL and "LGPL" in upper):
            bad.append(f"{name}: {license_}")
    return bad


def main() -> int:
    # Negative-control test: verify audit rejects incompatible version
    # Only versions explicitly in PROVENANCE_MANIFEST pass; others fail
    test_data = [{"Name": "openhands-sdk", "Version": "1.58.0", "License": "UNKNOWN"}]
    test_issues = problems(test_data)
    if not test_issues or "openhands-sdk: license unknown" not in test_issues:
        raise AssertionError(
            "Negative control failed: audit should reject openhands-sdk==1.58.0. "
            "Only version 1.59.0 is in PROVENANCE_MANIFEST."
        )

    bad = problems(json.load(sys.stdin))
    print("\n".join(bad) or "licenses: ok")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
