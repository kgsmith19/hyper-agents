"""Architecture: domain <- application <- adapters/interfaces, never the reverse.

The domain may import only the standard library, pydantic and itself. The
application layer may also import the domain, but no adapter, interface or
vendor SDK. This is what keeps the SDK, sandbox and storage swappable.
"""

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "hyper_agents"
STDLIB = set(sys.stdlib_module_names)


def imports_of(package: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in (SRC / package).rglob("*.py"):
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
        found[str(path.relative_to(SRC))] = names
    return found


def violations(
    package: str, allowed_third_party: set[str], allowed_internal: set[str]
) -> list[str]:
    bad: list[str] = []
    for file, names in imports_of(package).items():
        for name in names:
            top = name.split(".")[0]
            if top in STDLIB or top in allowed_third_party:
                continue
            if top == "hyper_agents" and any(
                name == f"hyper_agents.{p}" or name.startswith(f"hyper_agents.{p}.")
                for p in allowed_internal
            ):
                continue
            bad.append(f"{file} imports {name}")
    return bad


def test_domain_imports_no_vendor_db_or_framework_code() -> None:
    assert violations("domain", {"pydantic"}, {"domain"}) == []


def test_application_depends_only_on_the_domain() -> None:
    assert violations("application", {"pydantic"}, {"domain", "application"}) == []


def test_the_checker_catches_a_forbidden_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Negative control: the rule must be able to fail.
    fake = tmp_path / "hyper_agents" / "domain"
    fake.mkdir(parents=True)
    (fake / "leak.py").write_text(
        "import sqlite3\nimport openhands.sdk\nfrom hyper_agents.adapters import x\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path / "hyper_agents")
    assert sorted(violations("domain", {"pydantic"}, {"domain"})) == [
        "domain/leak.py imports hyper_agents.adapters",
        "domain/leak.py imports openhands.sdk",
    ]
