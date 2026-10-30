"""Dependency license audit: fail on a strong-copyleft or unknown license.

Reads `pip-licenses --format=json` on stdin. Our own package is skipped.
"""

import json
import sys

DENY = ("GPL", "AGPL", "SSPL", "EUPL")
ALLOW_LGPL = False


def problems(rows: list[dict[str, str]]) -> list[str]:
    bad: list[str] = []
    for row in rows:
        name, license_ = row.get("Name", "?"), row.get("License", "UNKNOWN")
        if name == "hyper-agents":
            continue
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
