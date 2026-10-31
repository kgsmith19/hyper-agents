"""Adapters: the only code that touches Infisical and GitHub. Values stay in memory.

Infisical auth: GitHub OIDC inside Actions (no stored client secret), or universal auth from the
environment for local runs. The GitHub adapter authenticates with a PAT that lives in Infisical.
"""

import json
import os
import shutil
import subprocess
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from hyper_agents.ops.core import fingerprint, redact

INFISICAL_API = "https://app.infisical.com/api"
INFISICAL_ENVIRONMENT = "prod"
OIDC_AUDIENCE = "https://github.com/kgsmith19"
PAT_PATH, PAT_NAME = "/ops/", "GITHUB_OPS_PAT"

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
