"""Adapters: the only code that touches Infisical and GitHub. Values stay in memory.

Infisical auth: GitHub OIDC inside Actions (no stored client secret), or universal auth from the
environment for local runs. The GitHub adapter authenticates with a PAT that lives in Infisical.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote

from hyper_agents.ops.core import fingerprint, redact

INFISICAL_API = "https://app.infisical.com/api"
INFISICAL_ENVIRONMENT = "prod"
# One listing per folder per run: every forge request reads its token through `get`, and
# re-listing on each call hit Infisical's rate limit (HTTP 429) during `maintain`.
SECRETS_CACHE_SECONDS = 300.0
OIDC_AUDIENCE = "https://github.com/kgsmith19"
PAT_PATH, PAT_NAME = "/ops/", "GITHUB_OPS_PAT"
SETTINGS_JQ = (
    "{default_branch, visibility, allow_auto_merge, allow_squash_merge, allow_merge_commit,"
    " allow_rebase_merge, delete_branch_on_merge}"
)

Runner = Callable[[list[str], str | None, dict[str, str]], tuple[int, str, str]]


def _post(url: str, body: Mapping[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - fixed Infisical HTTPS API
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def _get(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - fixed Infisical HTTPS API
        url, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def _send(method: str, url: str, token: str, body: Mapping[str, Any] | None) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - fixed Infisical HTTPS API
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        raw = response.read()
        return json.loads(raw) if raw else {}


# Built-in project roles an op may assign. Never "admin": no workload identity may hold it,
# and an agent must never be able to grant it (owner decision 2026-11-13, MB-009).
ASSIGNABLE_ROLES = ("member", "viewer", "no-access")
SIGNING_PATH = "/platform/lifeos-signing"
LIFEOS_BROKER_NAME = "ai-coding-harness"
# MB-011: forbid reading the private-key value on exactly the signing folder (and below).
SIGNING_RULE = "deny-read-value"
SIGNING_PRIVILEGE_SLUG = "lifeos-signing-deny"
SIGNING_DENY_RULES: list[dict[str, Any]] = [
    {
        "subject": "secrets",
        "inverted": True,
        "action": ["readValue"],
        "conditions": {"environment": INFISICAL_ENVIRONMENT, "secretPath": {"$eq": SIGNING_PATH}},
    },
    {
        "subject": "secrets",
        "inverted": True,
        "action": ["readValue"],
        "conditions": {
            "environment": INFISICAL_ENVIRONMENT,
            "secretPath": {"$glob": f"{SIGNING_PATH}/**"},
        },
    },
]
Sender = Callable[[str, str, str, Mapping[str, Any] | None], dict[str, Any]]


def _canonical_rules(rules: list[Any]) -> list[str]:
    """Order-free comparison of CASL rules; a string action equals a one-item list."""
    out: list[str] = []
    for rule in rules:
        item = dict(cast(Mapping[str, Any], rule))
        action = item.get("action")
        item["action"] = sorted([action] if isinstance(action, str) else list(action or []))
        item["inverted"] = bool(item.get("inverted"))
        conditions = cast(Mapping[str, Any], item.get("conditions") or {})
        item["conditions"] = {  # a bare value is Infisical's shorthand for {"$eq": value}
            key: value if isinstance(value, Mapping) else {"$eq": value}
            for key, value in conditions.items()
        }
        out.append(json.dumps(item, sort_keys=True))
    return sorted(out)


def _github_oidc_jwt() -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    request = urllib.request.Request(  # noqa: S310 - GitHub-provided endpoint
        f"{url}&audience={quote(OIDC_AUDIENCE, safe='')}",
        headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return str(json.load(response)["value"])


class InfisicalAdapter:
    def __init__(
        self,
        project_id: str,
        fetch: Callable[[str, str], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        send: Sender | None = None,
    ) -> None:
        self._project_id = project_id
        self._token: str | None = None
        self._fetch = fetch or _get
        self._send = send or _send
        self._clock = clock
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def _access_token(self) -> str:
        if self._token is None:
            if "ACTIONS_ID_TOKEN_REQUEST_URL" in os.environ:
                login = _post(
                    f"{INFISICAL_API}/v1/auth/oidc-auth/login",
                    {
                        "identityId": os.environ["INFISICAL_OPS_IDENTITY_ID"],
                        "jwt": _github_oidc_jwt(),
                    },
                )
            else:
                login = _post(
                    f"{INFISICAL_API}/v1/auth/universal-auth/login",
                    {
                        "clientId": os.environ["INFISICAL_CLIENT_ID"],
                        "clientSecret": os.environ["INFISICAL_CLIENT_SECRET"],
                    },
                )
            self._token = str(login["accessToken"])
        return self._token

    def _secrets(self, path: str) -> list[dict[str, Any]]:
        """The folder's secrets, listed at most once per ``SECRETS_CACHE_SECONDS``. A folder
        that does not exist (HTTP 404) lists as empty, so a lookup in it reads as missing."""
        cached = self._cache.get(path)
        if cached is not None and self._clock() - cached[0] < SECRETS_CACHE_SECONDS:
            return cached[1]
        url = (
            f"{INFISICAL_API}/v3/secrets/raw?workspaceId={quote(self._project_id)}"
            f"&environment={INFISICAL_ENVIRONMENT}&secretPath={quote(path, safe='')}"
        )
        try:
            items = list(self._fetch(url, self._access_token())["secrets"])
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            items = []
        self._cache[path] = (self._clock(), items)
        return items

    def get(self, path: str, name: str) -> str:
        for item in self._secrets(path):
            if item["secretKey"] == name:
                return str(item["secretValue"])
        raise LookupError(f"{name} not found at {path}")

    def upsert_secret(self, path: str, name: str, value: str) -> None:
        """Create or update one prod secret. PATCH first (the common case); only when the
        secret does not exist, create the folder path and POST. Any other error is
        reported with its own status. The value goes only into the request body."""
        if not value:
            raise ValueError(f"refusing to store an empty value for {path}/{name}")
        token = self._access_token()
        body = {
            "workspaceId": self._project_id,
            "environment": INFISICAL_ENVIRONMENT,
            "secretPath": path,
            "secretValue": value,
            "type": "shared",
        }
        url = f"{INFISICAL_API}/v3/secrets/raw/{quote(name)}"
        try:
            self._send("PATCH", url, token, body)
        except urllib.error.HTTPError as error:
            if error.code not in (400, 404):  # 404/400: the secret (or folder) is absent
                raise RuntimeError(f"could not update {path}/{name} (HTTP {error.code})") from None
            self._ensure_folders(path, token)
            try:
                self._send("POST", url, token, body)
            except urllib.error.HTTPError as create_error:
                raise RuntimeError(
                    f"could not create {path}/{name} (HTTP {create_error.code})"
                ) from None
        self._cache.pop(path, None)  # the next read sees the new value

    def _ensure_folders(self, path: str, token: str) -> None:
        parent = "/"
        for part in [p for p in path.split("/") if p]:
            try:
                self._send(
                    "POST",
                    f"{INFISICAL_API}/v1/folders",
                    token,
                    {
                        "workspaceId": self._project_id,
                        "environment": INFISICAL_ENVIRONMENT,
                        "name": part,
                        "path": parent,
                    },
                )
            except urllib.error.HTTPError as error:
                if error.code not in (400, 409):  # exists already
                    raise RuntimeError(
                        f"could not create folder {part} (HTTP {error.code})"
                    ) from None
            parent = parent.rstrip("/") + "/" + part

    def _memberships_url(self) -> str:
        return f"{INFISICAL_API}/v1/projects/{quote(self._project_id)}/identity-memberships"

    @staticmethod
    def _active_roles(raw: list[dict[str, Any]], now: datetime) -> list[str]:
        """Role slugs in force now: permanent ones, and temporary ones not yet expired."""
        roles: list[str] = []
        for role in raw:
            if role.get("isTemporary"):
                end = role.get("temporaryAccessEndTime")
                if not end or datetime.fromisoformat(str(end).replace("Z", "+00:00")) <= now:
                    continue
            roles.append(str(role.get("role")))
        return sorted(roles)

    def identity_roles(self) -> list[dict[str, Any]]:
        """Every machine identity in the project with its active role slugs, across all
        pages. Names and ids only; memberships without an id are skipped."""
        rows: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        limit, offset = 1000, 0
        while True:
            page = self._send(
                "GET",
                f"{self._memberships_url()}?limit={limit}&offset={offset}",
                self._access_token(),
                None,
            )
            found = cast(list[dict[str, Any]], page.get("identityMemberships") or [])
            for membership in found:
                identity = cast(dict[str, Any], membership.get("identity") or {})
                identity_id = membership.get("identityId") or identity.get("id")
                if not identity_id:
                    continue
                roles = cast(list[dict[str, Any]], membership.get("roles") or [])
                rows.append(
                    {
                        "id": str(identity_id),
                        "name": str(identity.get("name") or ""),
                        "roles": self._active_roles(roles, now),
                    }
                )
            offset += len(found)
            total = int(page.get("totalCount") or 0)
            if len(found) < limit or (total and offset >= total):
                break
        return sorted(rows, key=lambda row: row["name"])

    def _membership_roles(self, identity: str) -> list[str]:
        try:
            found = self._send(
                "GET", f"{self._memberships_url()}/{quote(identity)}", self._access_token(), None
            )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise LookupError(f"identity {identity} is not a member of this project") from None
            raise
        membership = cast(dict[str, Any], found.get("identityMembership") or {})
        return self._active_roles(
            cast(list[dict[str, Any]], membership.get("roles") or []), datetime.now(UTC)
        )

    def set_identity_role(self, identity: str, role: str) -> dict[str, Any]:
        """Demote one *admin* identity to a single built-in non-admin role. Refuses admin as
        the target role and refuses any identity that does not hold admin now, so the op can
        only remove admin, never lock out an ordinary identity.

        The broker may be demoting itself: once the PATCH lands it may no longer read
        memberships, so a 401/403 on the read-back reports ``unverified`` and the next
        maintenance check (a separate identity) confirms the result."""
        if role not in ASSIGNABLE_ROLES:
            raise ValueError(f"refusing role {role!r}; assignable: {list(ASSIGNABLE_ROLES)}")
        before = self._membership_roles(identity)
        if "admin" not in before:
            raise PermissionError(f"refusing: {identity} does not hold admin (roles {before})")
        self._send(
            "PATCH",
            f"{self._memberships_url()}/{quote(identity)}",
            self._access_token(),
            {"roles": [{"role": role, "isTemporary": False}]},
        )
        try:
            after = self._membership_roles(identity)
        except urllib.error.HTTPError as error:
            if error.code not in (401, 403):
                raise
            return {"identity": identity, "before": before, "after": "unverified (lost read)"}
        if after != [role]:
            raise RuntimeError(f"read-back shows roles {after} for {identity}")
        return {"identity": identity, "before": before, "after": after}

    def _privileges_url(self, privilege_id: str | None = None) -> str:
        base = f"{INFISICAL_API}/v2/identity-project-additional-privilege"
        return f"{base}/{quote(privilege_id)}" if privilege_id else base

    def _identity_row(self, identity: str) -> dict[str, Any]:
        row = next((item for item in self.identity_roles() if item["id"] == identity), None)
        if row is None:
            raise LookupError(f"identity {identity} is not a member of this project")
        return row

    def _signing_privilege(self, identity: str) -> dict[str, Any] | None:
        """The identity's ``lifeos-signing-deny`` privilege with its rules, or None.

        The list endpoint returns metadata only, so the rules come from a read by id."""
        listing = self._send(
            "GET",
            f"{self._privileges_url()}?identityId={quote(identity)}"
            f"&projectId={quote(self._project_id)}",
            self._access_token(),
            None,
        )
        rows = cast(list[dict[str, Any]], listing.get("privileges") or [])
        matches = [row for row in rows if row.get("slug") == SIGNING_PRIVILEGE_SLUG]
        if len(matches) > 1:
            raise RuntimeError(f"duplicate {SIGNING_PRIVILEGE_SLUG} privileges for {identity}")
        if not matches:
            return None
        one = self._send(
            "GET", self._privileges_url(str(matches[0]["id"])), self._access_token(), None
        )
        return cast(dict[str, Any], one.get("privilege") or {})

    @staticmethod
    def _privilege_state(privilege: Mapping[str, Any] | None) -> str:
        if privilege is None:
            return "missing"
        if privilege.get("isTemporary"):
            return "temporary"
        held = _canonical_rules(cast(list[Any], privilege.get("permissions") or []))
        return "deny" if held == _canonical_rules(SIGNING_DENY_RULES) else "drifted"

    def signing_folder_access_report(self) -> dict[str, Any]:
        """Privilege metadata only. The signing-key value is never requested."""
        protected: list[str] = []
        drift: list[dict[str, str]] = []
        for row in self.identity_roles():
            if row["name"] == LIFEOS_BROKER_NAME:
                continue
            state = self._privilege_state(self._signing_privilege(str(row["id"])))
            if state == "deny":
                protected.append(str(row["name"]))
                continue
            drift.append({"identity": str(row["id"]), "name": str(row["name"]), "state": state})
        return {
            "path": SIGNING_PATH,
            "broker": LIFEOS_BROKER_NAME,
            "protected": sorted(protected),
            "drift": sorted(drift, key=lambda item: item["name"]),
        }

    def restrict_signing_folder(self, identity: str, rule: str) -> dict[str, Any]:
        """Give one non-broker identity the permanent deny-readValue privilege, then read it back.

        Infisical permissions are additive; only an inverted (forbid) rule can narrow what a
        Viewer or Member role already allows. The rule forbids ``readValue`` on exactly the
        signing folder, so names and metadata stay visible and the key value does not.
        Rollback: DELETE the privilege by id (it holds nothing else)."""
        if rule != SIGNING_RULE:
            raise ValueError(f"only {SIGNING_RULE!r} may be applied to the LifeOS signing folder")
        row = self._identity_row(identity)
        if row["name"] == LIFEOS_BROKER_NAME:
            raise PermissionError("refusing to restrict the designated LifeOS token broker")
        current = self._signing_privilege(identity)
        state = self._privilege_state(current)
        body: dict[str, Any] = {
            "slug": SIGNING_PRIVILEGE_SLUG,
            "permissions": SIGNING_DENY_RULES,
            "type": {"isTemporary": False},
        }
        if state == "deny":
            action = "unchanged"
        elif current is None:
            action = "created"
            self._send(
                "POST",
                self._privileges_url(),
                self._access_token(),
                {"identityId": identity, "projectId": self._project_id, **body},
            )
        else:
            action = "updated"
            self._send(
                "PATCH", self._privileges_url(str(current["id"])), self._access_token(), body
            )
        after = self._signing_privilege(identity)
        if self._privilege_state(after) != "deny":
            raise RuntimeError(f"read-back did not show the permanent deny rule for {identity}")
        assert after is not None
        return {
            "identity": identity,
            "name": row["name"],
            "path": SIGNING_PATH,
            "rule": rule,
            "privilege_id": str(after.get("id") or ""),
            "action": action,
        }

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        if action == "access_report":
            rows = self.identity_roles()
            return {
                "identities": rows,
                "admins": [r["name"] for r in rows if "admin" in r["roles"]],
            }
        if action == "set_identity_role":
            return self.set_identity_role(params["identity"], params["role"])
        if action == "signing_folder_access_report":
            return self.signing_folder_access_report()
        if action == "restrict_signing_folder":
            return self.restrict_signing_folder(params["identity"], params["rule"])
        path = params["source_path"]
        if action == "exists":
            value = self.get(path, params["source_name"])
            return {"exists": True, "fingerprint": fingerprint(value)}
        if action == "list_names":
            return {"names": sorted(str(item["secretKey"]) for item in self._secrets(path))}
        raise ValueError(f"unknown infisical action: {action}")


class GithubAdapter:
    def __init__(self, infisical: InfisicalAdapter, run: Runner | None = None) -> None:
        self._infisical = infisical
        self._run = run or _subprocess_runner
        self._pat: str | None = None

    def _gh(self, argv: list[str], stdin: str | None = None) -> str:
        if self._pat is None:
            self._pat = self._infisical.get(PAT_PATH, PAT_NAME)
        returncode, stdout, stderr = self._run(
            ["gh", *argv], stdin, {**os.environ, "GH_TOKEN": self._pat}
        )
        if returncode != 0:
            secrets = [self._pat] + ([stdin] if stdin else [])
            raise RuntimeError(redact(f"gh {argv[0]} {argv[1]} failed: {stderr.strip()}", secrets))
        return stdout

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        repo = params["repo"]
        if action == "list_secrets":
            names = json.loads(self._gh(["secret", "list", "-R", repo, "--json", "name"]))
            return {"names": sorted(str(item["name"]) for item in names)}
        if action == "set_secret":
            value = self._infisical.get(params["source_path"], params["source_name"])
            self._gh(["secret", "set", params["name"], "-R", repo], stdin=value)
            return {"name": params["name"], "fingerprint": fingerprint(value)}
        if action == "delete_secret":
            try:
                self._gh(["secret", "delete", params["name"], "-R", repo])
            except RuntimeError as error:
                # Idempotent: a secret that is already absent (GitHub's HTTP 404) is the
                # desired end state, not a failure. Every other error still fails the op.
                if "HTTP 404" not in str(error):
                    raise
                return {"name": params["name"], "deleted": False, "already_absent": True}
            return {"name": params["name"], "deleted": True}
        if action == "set_variable":
            self._gh(["variable", "set", params["name"], "-R", repo], stdin=params["value"])
            return {"name": params["name"]}
        if action == "list_variables":
            names = json.loads(self._gh(["variable", "list", "-R", repo, "--json", "name"]))
            return {"names": sorted(str(item["name"]) for item in names)}
        if action == "delete_variable":
            self._gh(["variable", "delete", params["name"], "-R", repo])
            return {"name": params["name"], "deleted": True}
        if action == "get_settings":
            # P3, read-only: a fixed field selection, never the whole API object.
            fields = json.loads(self._gh(["api", f"repos/{repo}", "--jq", SETTINGS_JQ]))
            return {"settings": fields}
        raise ValueError(f"unknown github action: {action}")


def _subprocess_runner(
    argv: list[str], stdin: str | None, env: dict[str, str]
) -> tuple[int, str, str]:
    executable = shutil.which(argv[0])
    if executable is None:
        raise RuntimeError(f"{argv[0]} is not installed on this runner")
    completed = subprocess.run(  # noqa: S603 - fixed argv built by this module
        [executable, *argv[1:]], input=stdin, capture_output=True, text=True, env=env, check=False
    )
    return completed.returncode, completed.stdout, completed.stderr


# P2 (agent-extensions#100): live vendor key validation with one cheap read call.
# Hosts are fixed here, never taken from params, so the adapter cannot be aimed at
# an arbitrary URL (the http allowlist).
VENDOR_ENDPOINTS: dict[str, tuple[str, Callable[[str], dict[str, str]]]] = {
    "openai": (
        "https://api.openai.com/v1/models",
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
    "anthropic": (
        "https://api.anthropic.com/v1/models",
        lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
    ),
}

HttpStatus = Callable[[str, dict[str, str]], int]


def _http_status(url: str, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - fixed vendor hosts
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


class VendorAdapter:
    def __init__(self, infisical: InfisicalAdapter, status: HttpStatus | None = None) -> None:
        self._infisical = infisical
        self._status = status or _http_status

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        if action != "validate_key":
            raise ValueError(f"unknown vendor action: {action}")
        provider = params["provider"]
        if provider not in VENDOR_ENDPOINTS:
            raise ValueError(f"unknown provider: {provider}")
        url, headers = VENDOR_ENDPOINTS[provider]
        key = self._infisical.get(params["source_path"], params["source_name"])
        try:
            code = self._status(url, headers(key))
        except Exception as error:
            raise RuntimeError(redact(f"{provider} validation failed: {error}", [key])) from None
        return {
            "provider": provider,
            "valid": code == 200,
            "http_status": code,
            "fingerprint": fingerprint(key),
        }


# P4: a fixed argv allowlist. Params pick a key; input never becomes argv or a shell string.
CLI_ALLOWLIST: dict[str, list[str]] = {
    "gh-version": ["gh", "--version"],
    "git-version": ["git", "--version"],
}


class CliAdapter:
    def __init__(self, run: Runner | None = None) -> None:
        self._run = run or _subprocess_runner

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        if action != "run":
            raise ValueError(f"unknown cli action: {action}")
        argv = CLI_ALLOWLIST.get(params["command"])
        if argv is None:
            raise ValueError(f"command is not allowlisted: {params['command']!r}")
        returncode, stdout, _stderr = self._run(list(argv), None, dict(os.environ))
        return {"command": params["command"], "returncode": returncode, "stdout": stdout[:2000]}
