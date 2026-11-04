"""The tracker only reads, and only from the GitHub API (P04-3)."""

import pytest

from hyper_agents.adapters.github_tracker import GithubTracker, UrllibHttpGet
from hyper_agents.domain.errors import InvalidInput, PolicyDenied


class FakeHttp:
    def __init__(self, body: object) -> None:
        self.body = body
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, url: str, headers: dict[str, str]) -> object:
        self.calls.append((url, headers))
        return self.body


ISSUES: list[object] = [
    {
        "number": 9,
        "title": "Second",
        "html_url": "u9",
        "labels": [{"name": "agent"}],
        "updated_at": "t",
    },
    {
        "number": 3,
        "title": "First",
        "html_url": "u3",
        "labels": [{"name": "agent"}, {"name": "bug"}],
    },
    {"number": 5, "title": "A PR", "pull_request": {"url": "x"}, "labels": []},
    "junk",
]


def test_poll_lists_issues_skips_pull_requests_and_sorts() -> None:
    http = FakeHttp(ISSUES)
    items = GithubTracker(http, "kgsmith19/hyper-agents", "agent").poll()
    assert [i.number for i in items] == [3, 9]
    assert items[0].labels == ("agent", "bug")
    ((url, headers),) = http.calls
    assert url.startswith("https://api.github.com/repos/kgsmith19/hyper-agents/issues?state=open")
    assert "labels=agent" in url
    assert "Authorization" not in headers


def test_a_token_is_sent_as_a_bearer_header_only() -> None:
    http = FakeHttp([])
    GithubTracker(http, "o/r", "agent", token="placeholder-not-a-secret").poll()  # noqa: S106
    ((url, headers),) = http.calls
    assert headers["Authorization"] == "Bearer placeholder-not-a-secret"
    assert "placeholder" not in url


def test_a_non_list_response_is_refused() -> None:
    with pytest.raises(InvalidInput):
        GithubTracker(FakeHttp({"message": "Bad credentials"}), "o/r", "agent").poll()


@pytest.mark.parametrize("repo", ["o", "o/r/x", "../r", "o/r?x=1"])
def test_a_malformed_repository_is_refused(repo: str) -> None:
    with pytest.raises(InvalidInput):
        GithubTracker(FakeHttp([]), repo, "agent")


def test_a_label_cannot_inject_query_parameters() -> None:
    with pytest.raises(InvalidInput):
        GithubTracker(FakeHttp([]), "o/r", "agent&state=closed")


@pytest.mark.parametrize(
    "url",
    ["http://api.github.com/repos", "https://evil.example/repos", "file:///etc/passwd"],
)
def test_the_http_adapter_reads_only_the_github_api(url: str) -> None:
    with pytest.raises(PolicyDenied):
        UrllibHttpGet().get_json(url, {})


def test_the_http_port_has_no_write_verb() -> None:
    public = {name for name in dir(UrllibHttpGet) if not name.startswith("_")}
    assert public == {"get_json"}
