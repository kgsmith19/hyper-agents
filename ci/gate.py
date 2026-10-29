"""PR Gate rollup: pass only when every mandated lane concluded exactly `success`.

Reads the workflow's `toJSON(needs)` on stdin. A lane that is missing, skipped,
cancelled, neutral or failed fails the gate: anything that is not proven green
is red. Exits 0 on pass, 1 on fail, and prints one line per lane.
"""

import json
import sys

MANDATED = ("quality", "tests", "licenses", "tested-head")


def verdict(needs: dict[str, dict[str, object]]) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    for lane in MANDATED:
        result = needs.get(lane, {}).get("result", "missing")
        passed = result == "success"
        ok = ok and passed
        lines.append(f"{'PASS' if passed else 'FAIL'} {lane}: {result}")
    return ok, lines


def main() -> int:
    ok, lines = verdict(json.load(sys.stdin))
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
