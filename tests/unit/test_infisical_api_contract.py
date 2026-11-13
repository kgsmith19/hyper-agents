"""Every Infisical endpoint the ops adapter calls must exist in the live API (#88).

#86 shipped a call to a path Infisical does not serve; the check failed on every maintain run
with nothing but "HTTPError". This pins each path the adapter builds to a vendored snapshot of
the public OpenAPI path list (refresh: scripts/refresh-infisical-paths.sh).
"""

import re
from pathlib import Path

from hyper_agents.ops.adapters import INFISICAL_API, InfisicalAdapter

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/hyper_agents/ops/adapters.py"
SPEC = ROOT / "tests/fixtures/infisical_openapi_paths.txt"


def _spec_patterns() -> list[re.Pattern[str]]:
    paths = [line.strip() for line in SPEC.read_text().splitlines() if line.strip()]
    return [re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(p)) + "$") for p in paths]


def _served(path: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.match(path) for p in patterns)


def _literal_paths() -> list[str]:
    """Each f-string URL in the adapter source, with {expressions} turned into one segment."""
    found = re.findall(r'f"\{INFISICAL_API\}(/[^"?]*)', SOURCE.read_text())
    return ["/api" + re.sub(r"\{[^}]+\}", "x", path) for path in found]


def test_snapshot_is_present_and_plausible() -> None:
    patterns = _spec_patterns()
    assert len(patterns) > 500
    assert _served("/api/v3/secrets/raw", patterns)


def test_every_literal_adapter_path_exists_in_the_live_api() -> None:
    patterns = _spec_patterns()
    literals = _literal_paths()
    assert len(literals) >= 7
    missing = [path for path in literals if not _served(path, patterns)]
    assert missing == []


def test_derived_adapter_paths_exist_in_the_live_api() -> None:
    patterns = _spec_patterns()
    adapter = InfisicalAdapter("00000000-0000-4000-8000-000000000000")
    base = INFISICAL_API.removesuffix("/api")
    derived = [
        adapter._memberships_url(),  # pyright: ignore[reportPrivateUsage]
        adapter._memberships_url() + "/00000000-0000-4000-8000-000000000001",  # pyright: ignore[reportPrivateUsage]
        adapter._privileges_url(),  # pyright: ignore[reportPrivateUsage]
        adapter._privileges_url("00000000-0000-4000-8000-000000000002"),  # pyright: ignore[reportPrivateUsage]
    ]
    missing = [url for url in derived if not _served(url.removeprefix(base), patterns)]
    assert missing == []


def test_the_86_endpoint_would_have_been_caught() -> None:
    bad = "/api/v1/projects/p/memberships/identities/i/secret-folder-access"
    assert not _served(bad, _spec_patterns())
