"""Fetch one secret from Infisical (/dev/, prod) and export it to later CI steps.

Usage: fetch_infisical_secret.py <InfisicalSecretName> <EXPORTED_ENV_NAME>

Reads INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET and INFISICAL_PROJECT_ID from the environment.
The value is masked in logs and written to $GITHUB_ENV. It is never printed.
"""

import json
import os
import sys
import urllib.request
from typing import Any
from urllib.parse import quote

BASE = "https://app.infisical.com/api"
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


def main() -> int:
    secret_name, export_name = sys.argv[1], sys.argv[2]
    client_id = os.environ.get("INFISICAL_CLIENT_ID", "")
    client_secret = os.environ.get("INFISICAL_CLIENT_SECRET", "")
    project_id = os.environ.get("INFISICAL_PROJECT_ID", "")
    if not (client_id and client_secret and project_id):
        print("INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET and INFISICAL_PROJECT_ID must be set")
        return 1

    login = _post_json(
        f"{BASE}/v1/auth/universal-auth/login",
        {"clientId": client_id, "clientSecret": client_secret},
    )
    token = str(login["accessToken"])
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
