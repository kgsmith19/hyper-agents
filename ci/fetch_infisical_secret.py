"""Fetch one secret from Infisical (/dev/, prod) and export it to later CI steps.

Usage: fetch_infisical_secret.py <InfisicalSecretName> <EXPORTED_ENV_NAME>

Auth: inside GitHub Actions with `id-token: write`, logs in with GitHub OIDC as the Infisical
identity in INFISICAL_IDENTITY_ID (no stored client secret). Otherwise uses universal auth from
INFISICAL_CLIENT_ID and INFISICAL_CLIENT_SECRET. INFISICAL_PROJECT_ID is always required.
The value is masked in logs and written to $GITHUB_ENV. It is never printed.
"""

import json
import os
import sys
import urllib.request
from typing import Any
from urllib.parse import quote

BASE = "https://app.infisical.com/api"
OIDC_AUDIENCE = "https://github.com/kgsmith19"
INFISICAL_PATH = "/dev/"
ENVIRONMENT = "prod"


def _post_json(url: str, body: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - fixed Infisical HTTPS API
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def _get_json(url: str, token: str) -> dict[str, Any]:
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


def _login() -> str | None:
    identity_id = os.environ.get("INFISICAL_IDENTITY_ID", "")
    if identity_id and "ACTIONS_ID_TOKEN_REQUEST_URL" in os.environ:
        login = _post_json(
            f"{BASE}/v1/auth/oidc-auth/login",
            {"identityId": identity_id, "jwt": _github_oidc_jwt()},
        )
        return str(login["accessToken"])
    client_id = os.environ.get("INFISICAL_CLIENT_ID", "")
    client_secret = os.environ.get("INFISICAL_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        return None
    login = _post_json(
        f"{BASE}/v1/auth/universal-auth/login",
        {"clientId": client_id, "clientSecret": client_secret},
    )
    return str(login["accessToken"])


def main() -> int:
    secret_name, export_name = sys.argv[1], sys.argv[2]
    project_id = os.environ.get("INFISICAL_PROJECT_ID", "")
    token = _login() if project_id else None
    if not (project_id and token):
        print(
            "INFISICAL_PROJECT_ID and either INFISICAL_IDENTITY_ID (OIDC in Actions) or"
            " INFISICAL_CLIENT_ID and INFISICAL_CLIENT_SECRET must be set"
        )
        return 1
    url = (
        f"{BASE}/v3/secrets/raw?workspaceId={quote(project_id)}"
        f"&environment={ENVIRONMENT}&secretPath={quote(INFISICAL_PATH, safe='')}"
    )
    secrets = _get_json(url, token)["secrets"]
    value = next(
        (str(item["secretValue"]) for item in secrets if item["secretKey"] == secret_name),
        "",
    )
    if not value:
        print(f"{secret_name} not found or empty at {INFISICAL_PATH} ({ENVIRONMENT})")
        return 1

    print(f"::add-mask::{value}")
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env_file:
        env_file.write(f"{export_name}={value}\n")
    print(f"exported {export_name} from {secret_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
