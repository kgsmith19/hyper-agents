"""``ForgePort`` for GitHub's REST API. The only module that knows GitHub's URLs and fields.

Stdlib HTTP only. The token comes from a caller-supplied function (the secrets port), so this
adapter never chooses a vault. Requests go only to ``api``; the default is GitHub's public API.
"""

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from urllib.parse import quote, urlsplit

from hyper_agents.domain.forge import Access, Change, Item

_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SETTINGS_FIELDS = (
    "allow_squash_merge",
    "allow_merge_commit",
    "allow_rebase_merge",
    "allow_auto_merge",
    "delete_branch_on_merge",
    "allow_update_branch",
    "squash_merge_commit_title",
    "squash_merge_commit_message",
    "has_issues",
)

# A transport returns (status, body), or (status, body, headers) when it can see headers.
Transport = Callable[
    [str, str, dict[str, str], bytes | None],
    tuple[int, bytes] | tuple[int, bytes, dict[str, str]],
]
EXPIRY_HEADER = "github-authentication-token-expiration"
_MAX_PAGES = 50


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            seen = {k.lower(): v for k, v in response.headers.items()}
            return int(response.status), response.read(), seen
    except urllib.error.HTTPError as error:
        return int(error.code), error.read(), {k.lower(): v for k, v in error.headers.items()}


def _when(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GithubForge:
    def __init__(
        self,
        token: Callable[[], str],
        api: str = "https://api.github.com",
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._sleep = sleep
        self._api = api.rstrip("/")
        self._host = urlsplit(self._api).netloc
        self._transport = transport or _urllib_transport
        self._last_headers: dict[str, str] = {}

    def _send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        result = self._transport(method, url, headers, body)
        self._last_headers = dict(result[2]) if len(result) > 2 else {}  # type: ignore[misc]
        return result[0], result[1]

    def _call(self, method: str, path: str, payload: object | None = None) -> Any:
        status, raw = self._status_call(method, path, payload)
        if status >= 400:
            raise RuntimeError(f"{method} {path} failed with HTTP {status}")
        return json.loads(raw) if raw else None

    def _status_call(
        self, method: str, path: str, payload: object | None = None
    ) -> tuple[int, bytes]:
        url = f"{self._api}{path}"
        if urlsplit(url).netloc != self._host:
            raise ValueError("refusing a request outside the configured API host")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {self._token()}",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, raw = self._send(method, url, headers, body)
        # Rate limited: back off and retry, at most three times (5s, 20s, 60s).
        for delay in (5.0, 20.0, 60.0):
            if status != 429:
                break
            self._sleep(delay)
            status, raw = self._send(method, url, headers, body)
        return status, raw

    @staticmethod
    def _repo(repo: str) -> str:
        if not _REPO.fullmatch(repo):
            raise ValueError(f"not an owner/name repository: {repo!r}")
        return repo

    def merged_changes_since(self, repo: str, since: datetime) -> list[Change]:
        path = (
            f"/repos/{self._repo(repo)}/pulls?state=closed&sort=updated&direction=desc&per_page=100"
        )
        changes: list[Change] = []
        for raw in cast(list[dict[str, Any]], self._call("GET", path) or []):
            merged_at = _when(raw.get("merged_at"))
            if merged_at is None or merged_at < since:
                continue
            changes.append(
                Change(
                    number=int(raw["number"]),
                    body=str(raw.get("body") or ""),
                    head_ref=str(raw.get("head", {}).get("ref", "")),
                    merged_at=merged_at,
                )
            )
        return changes

    def open_changes(self, repo: str) -> list[Change]:
        listing = cast(
            list[dict[str, Any]],
            self._call("GET", f"/repos/{self._repo(repo)}/pulls?state=open&per_page=100") or [],
        )
        changes: list[Change] = []
        for summary in listing:
            # mergeable_state is only on the single-change read, not on the listing.
            raw = cast(
                dict[str, Any], self._call("GET", f"/repos/{repo}/pulls/{summary['number']}")
            )
            state = str(raw.get("mergeable_state") or "")
            changes.append(
                Change(
                    number=int(raw["number"]),
                    body=str(raw.get("body") or ""),
                    head_ref=str(raw.get("head", {}).get("ref", "")),
                    conflicted=state == "dirty",
                    behind=state == "behind",
                    labels=tuple(
                        str(label["name"])
                        for label in cast(list[dict[str, Any]], raw.get("labels") or [])
                    ),
                )
            )
        return changes

    def item(self, repo: str, number: int) -> Item | None:
        try:
            raw = cast(
                dict[str, Any], self._call("GET", f"/repos/{self._repo(repo)}/issues/{number}")
            )
        except RuntimeError:
            return None
        if "pull_request" in raw:
            return None
        return Item(number=number, open=raw.get("state") == "open")

    def reopened_since(self, repo: str, number: int, since: datetime) -> bool:
        events = cast(
            list[dict[str, Any]],
            self._call("GET", f"/repos/{self._repo(repo)}/issues/{number}/events?per_page=100")
            or [],
        )
        return any(
            event.get("event") == "reopened" and (_when(event.get("created_at")) or since) > since
            for event in events
        )

    def close_item(self, repo: str, number: int, note: str) -> None:
        self._call("POST", f"/repos/{self._repo(repo)}/issues/{number}/comments", {"body": note})
        self._call(
            "PATCH",
            f"/repos/{repo}/issues/{number}",
            {"state": "closed", "state_reason": "completed"},
        )

    def set_label(self, repo: str, number: int, label: str, present: bool) -> None:
        if present:
            self._call(
                "POST", f"/repos/{self._repo(repo)}/issues/{number}/labels", {"labels": [label]}
            )
        else:
            self._call("DELETE", f"/repos/{self._repo(repo)}/issues/{number}/labels/{quote(label)}")

    def upsert_note(self, repo: str, number: int, marker: str, text: str) -> None:
        base = f"/repos/{self._repo(repo)}/issues"
        body = f"{marker}\n{text}"
        for comment in self._comments(repo, number):
            if str(comment.get("body") or "").startswith(marker):
                if comment.get("body") != body:
                    self._call("PATCH", f"{base}/comments/{comment['id']}", {"body": body})
                return
        self._call("POST", f"{base}/{number}/comments", {"body": body})

    def update_change_branch(self, repo: str, number: int) -> None:
        self._call("PUT", f"/repos/{self._repo(repo)}/pulls/{number}/update-branch", {})

    def settings(self, repo: str) -> dict[str, object]:
        raw = cast(dict[str, Any], self._call("GET", f"/repos/{self._repo(repo)}"))
        return {name: raw.get(name) for name in SETTINGS_FIELDS}

    def set_settings(self, repo: str, changes: dict[str, object]) -> None:
        unknown = sorted(set(changes) - set(SETTINGS_FIELDS))
        if unknown:
            raise ValueError(f"unsupported settings: {unknown}")
        self._call("PATCH", f"/repos/{self._repo(repo)}", changes)

    def read_file(self, repo: str, ref: str, path: str) -> str:
        import base64

        raw = cast(
            dict[str, Any],
            self._call("GET", f"/repos/{self._repo(repo)}/contents/{quote(path)}?ref={quote(ref)}"),
        )
        return base64.b64decode(str(raw["content"])).decode("utf-8")

    def access(self, repo: str) -> Access:
        """Probe without changing anything: read settings, and add an empty label list to
        the newest item (a no-op write that still needs item write permission)."""
        settings_readable = any(value is not None for value in self.settings(repo).values())
        expires = _expiry(self._last_headers.get(EXPIRY_HEADER))
        newest = cast(
            list[dict[str, Any]],
            self._call("GET", f"/repos/{self._repo(repo)}/issues?state=all&per_page=1") or [],
        )
        items_writable = False
        if newest:
            status, _ = self._status_call(
                "POST",
                f"/repos/{self._repo(repo)}/issues/{newest[0]['number']}/labels",
                {"labels": []},
            )
            # GitHub rejects the empty label list with 422 only after authorizing the write,
            # so 422 proves write access; 401/403/404 do not.
            items_writable = status < 400 or status == 422
        return Access(settings_readable, items_writable, expires)

    def ensure_item(self, repo: str, marker: str, title: str, body: str, open: bool) -> int:
        """Find the item whose body starts with ``marker`` (any state) and make it match;
        create it when missing. Returns its number."""
        base = f"/repos/{self._repo(repo)}/issues"
        state = "open" if open else "closed"
        for page in range(1, 6):
            found = cast(
                list[dict[str, Any]],
                self._call("GET", f"{base}?state=all&per_page=100&page={page}") or [],
            )
            for item in found:
                if "pull_request" in item or not str(item.get("body") or "").startswith(marker):
                    continue
                changes: dict[str, object] = {}
                if item.get("body") != body:
                    changes["body"] = body
                if item.get("title") != title:
                    changes["title"] = title
                if item.get("state") != state:
                    changes["state"] = state
                    if not open:
                        changes["state_reason"] = "completed"
                if changes:
                    self._call("PATCH", f"{base}/{item['number']}", changes)
                return int(item["number"])
            if len(found) < 100:
                break
        created = cast(dict[str, Any], self._call("POST", base, {"title": title, "body": body}))
        number = int(created["number"])
        if not open:
            self._call("PATCH", f"{base}/{number}", {"state": "closed"})
        return number

    def note_exists(self, repo: str, number: int, marker: str) -> bool:
        return any(
            str(c.get("body") or "").startswith(marker) for c in self._comments(repo, number)
        )

    def _comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        """Every comment on an item, across pages, so marker dedupe holds past 100 comments."""
        base = f"/repos/{self._repo(repo)}/issues/{number}/comments?per_page=100"
        comments: list[dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            found = cast(list[dict[str, Any]], self._call("GET", f"{base}&page={page}") or [])
            comments.extend(found)
            if len(found) < 100:
                return comments
        raise RuntimeError(f"more than {_MAX_PAGES * 100} comments on item {number}")

    def dispatch(self, repo: str, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        self._call(
            "POST",
            f"/repos/{self._repo(repo)}/actions/workflows/{quote(workflow)}/dispatches",
            {"ref": ref, "inputs": inputs},
        )


def _expiry(value: str | None) -> datetime | None:
    """Parse the credential expiry header ("2027-11-13 09:00:00 UTC"), if present."""
    if not value:
        return None
    try:
        return datetime.strptime(value.replace(" UTC", " +0000"), "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None
