"""No credential material is ever committed (docs/design/v1.md, "Secrets")."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]
SELF = Path(__file__).resolve()


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 (git from PATH, fixed argv)
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [ROOT / name for name in out.split("\0") if name]


def test_no_tracked_file_contains_credential_material() -> None:
    hits: list[str] = []
    for path in tracked_files():
        if path.resolve() == SELF or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{path.relative_to(ROOT)}: {p.pattern}" for p in PATTERNS if p.search(text)]
    assert hits == []


def test_no_env_file_is_tracked() -> None:
    names = {p.name for p in tracked_files()}
    assert not {n for n in names if n == ".env" or (n.startswith(".env.") and n != ".env.example")}
