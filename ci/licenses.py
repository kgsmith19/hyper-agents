"""Dependency license audit: fail on a strong-copyleft or unknown license.

Reads `pip-licenses --format=json` on stdin. Our own package is skipped.

PROVENANCE_MANIFEST records the license of an exact name==version, verified against
upstream source. It overrides PyPI metadata, which is blank for these packages. A
different version has no record, so it fails until someone verifies it.
"""

import json
import sys

DENY = ("GPL", "AGPL", "SSPL", "EUPL")
ALLOW_LGPL = False

PROVENANCE_MANIFEST = {
    "openhands-sdk==1.58.2": {
        "license": "MIT",
        "source": "https://github.com/OpenHands/software-agent-sdk/blob/v1.58.2/LICENSE",
    },
    "openhands-workspace==1.58.2": {
        "license": "MIT",
        "source": "https://github.com/OpenHands/software-agent-sdk/blob/v1.58.2/LICENSE",
    },
    "openhands-agent-server==1.49.5": {
        "license": "MIT",
        "source": "https://github.com/OpenHands/software-agent-sdk/blob/v1.58.2/LICENSE",
    },
    "agent-client-protocol==0.12.1": {
        "license": "Apache-2.0",
        "source": "LICENSE inside the 0.12.1 wheel (dist-info/licenses/LICENSE)",
    },
}


def problems(rows: list[dict[str, str]]) -> list[str]:
    bad: list[str] = []
    for row in rows:
        name, version = row.get("Name", "?"), row.get("Version", "?")
        license_ = row.get("License", "UNKNOWN")

        if name == "hyper-agents":
            continue

        record = PROVENANCE_MANIFEST.get(f"{name}=={version}")
        if record is not None:
            license_ = record["license"]

        upper = license_.upper()
        if upper in ("", "UNKNOWN"):
            bad.append(f"{name}: license unknown")
        elif any(term in upper for term in DENY) and not (ALLOW_LGPL and "LGPL" in upper):
            bad.append(f"{name}: {license_}")
    return bad


def main() -> int:
    bad = problems(json.load(sys.stdin))
    print("\n".join(bad) or "licenses: ok")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
