"""Read-only GitHub tracker (P04-3): poll open Issues with one label.

It is a reader by construction. The only port it holds is ``HttpGetPort``,
whose single method is a GET, and the stdlib adapter below refuses any URL
that is not ``https://api.github.com``. Pull requests come back from the
Issues endpoint too; they are skipped. What it returns is plain data, and an
Issue body is never read, so repository content cannot steer the controller
from here.
"""

import json
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from hyper_agents.application.ports import HttpGetPort
from hyper_agents.domain.errors import InvalidInput, PolicyDenied

API = "https://api.github.com"
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_LABEL = re.compile(r"^[A-Za-z0-9 :_./-]{1,50}$")


@dataclass(frozen=True)
class WorkItem:
    repo: str
    number: int
    title: str
    url: str
    labels: tuple[str, ...]
    updated_at: str


class GithubTracker:
    def __init__(self, http: HttpGetPort, repo: str, label: str, token: str | None = None) -> None:
        if not _REPO.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/")):
            raise InvalidInput(f"not an owner/name repository: {repo!r}")
        if not _LABEL.fullmatch(label):
            raise InvalidInput(f"unsupported label: {label!r}")
        self._http = http
        self._repo = repo
        self._label = label
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def poll(self) -> list[WorkItem]:
        url = f"{API}/repos/{self._repo}/issues?state=open&per_page=100&labels={quote(self._label)}"
        raw = self._http.get_json(url, dict(self._headers))
        if not isinstance(raw, list):
            raise InvalidInput("issues response is not a list")
        items: list[WorkItem] = []
        for entry in raw:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(entry, dict) or "pull_request" in entry:
                continue
            labels = tuple(
                str(label["name"])  # pyright: ignore[reportUnknownArgumentType]
                for label in entry.get("labels", [])  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
                if isinstance(label, dict) and "name" in label
            )
            items.append(
                WorkItem(
                    repo=self._repo,
                    number=int(entry["number"]),  # pyright: ignore[reportUnknownArgumentType]
                    title=str(entry.get("title", ""))[:200],  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    url=str(entry.get("html_url", "")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    labels=labels,
                    updated_at=str(entry.get("updated_at", "")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                )
            )
        return sorted(items, key=lambda item: item.number)


class UrllibHttpGet:
    """GET-only HTTPS to the GitHub API, with a timeout. No other verb exists here."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    def get_json(self, url: str, headers: dict[str, str]) -> object:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "api.github.com":
            raise PolicyDenied(f"tracker may only read {API}")
        request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 -- https only, checked above
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
