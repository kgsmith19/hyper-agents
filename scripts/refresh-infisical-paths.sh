#!/usr/bin/env bash
# Refresh the vendored Infisical OpenAPI path list used by tests/unit/test_infisical_api_contract.py.
# Public spec, no credentials. Re-run when Infisical changes its API, then commit the diff.
set -euo pipefail
out="$(dirname "$0")/../tests/fixtures/infisical_openapi_paths.txt"
curl -fsS https://app.infisical.com/api/docs/json \
  | python3 -I -c 'import json,sys; print("\n".join(sorted(json.load(sys.stdin)["paths"])))' > "$out"
wc -l "$out"
