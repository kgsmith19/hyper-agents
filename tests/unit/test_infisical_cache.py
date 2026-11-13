"""The Infisical adapter lists a folder once per run (no 429s) and treats a missing folder
as missing secrets."""

import io
import urllib.error
from typing import Any

import pytest

from hyper_agents.ops.adapters import SECRETS_CACHE_SECONDS, InfisicalAdapter


class Counting:
    def __init__(self, status: int = 200) -> None:
        self.urls: list[str] = []
        self.status = status

    def __call__(self, url: str, token: str) -> dict[str, Any]:
        self.urls.append(url)
        if self.status != 200:
            raise urllib.error.HTTPError(url, self.status, "x", {}, io.BytesIO(b""))  # type: ignore[arg-type]
        return {"secrets": [{"secretKey": "TOKEN", "secretValue": "v"}]}


class NoLogin(InfisicalAdapter):
    def _access_token(self) -> str:  # unit tests never log in
        return "unit-test"


def adapter(fetch: Counting, now: list[float]) -> InfisicalAdapter:
    return NoLogin("p", fetch=fetch, clock=lambda: now[0])


def test_repeated_reads_list_the_folder_once_until_the_cache_expires() -> None:
    fetch, now = Counting(), [0.0]
    infisical = adapter(fetch, now)
    assert [infisical.get("/ops", "TOKEN") for _ in range(50)] == ["v"] * 50
    assert len(fetch.urls) == 1
    now[0] = SECRETS_CACHE_SECONDS + 1
    infisical.get("/ops", "TOKEN")
    assert len(fetch.urls) == 2  # refreshed: a rotated token is picked up


def test_a_missing_folder_reads_as_a_missing_secret() -> None:
    infisical = adapter(Counting(status=404), [0.0])
    with pytest.raises(LookupError):
        infisical.get("/lifeos/agents", "CLAUDE_CODE_TOKEN")


def test_other_errors_still_raise() -> None:
    infisical = adapter(Counting(status=429), [0.0])
    with pytest.raises(urllib.error.HTTPError):
        infisical.get("/ops", "TOKEN")
