"""Adapters: the only code that touches Infisical and GitHub. Values stay in memory.

Infisical auth: GitHub OIDC inside Actions (no stored client secret), or universal auth from the
environment for local runs. The GitHub adapter authenticates with a PAT that lives in Infisical.
"""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from hyper_agents.ops.core import fingerprint, redact

INFISICAL_API = "https://app.infisical.com/api"
INFISICAL_ENVIRONMENT = "prod"
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


def _github_oidc_jwt() -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    request = urllib.request.Request(  # noqa: S310 - GitHub-provided endpoint
        f"{url}&audience={quote(OIDC_AUDIENCE, safe='')}",
        headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return str(json.load(response)["value"])


class InfisicalAdapter:
    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._token: str | None = None

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
        url = (
            f"{INFISICAL_API}/v3/secrets/raw?workspaceId={quote(self._project_id)}"
            f"&environment={INFISICAL_ENVIRONMENT}&secretPath={quote(path, safe='')}"
        )
        return list(_get(url, self._access_token())["secrets"])

    def get(self, path: str, name: str) -> str:
        for item in self._secrets(path):
            if item["secretKey"] == name:
                return str(item["secretValue"])
        raise LookupError(f"{name} not found at {path}")

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
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
            self._gh(["secret", "delete", params["name"], "-R", repo])
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
