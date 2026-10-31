# Ops broker runbook

The ops broker lets an agent request registered secret and config operations. A GitHub Actions
workflow runs them, reads credentials from Infisical, and writes a receipt. Agents never see
secret values. Design and decision record: agent-extensions#100 (decision record comment).

## How a request flows

1. An agent dispatches `.github/workflows/ops.yml` with `op`, `params` (JSON) and `mode`
   (`dry-run` or `apply`). The GitHub MCP `actions_run_trigger` or `gh workflow run` both work.
2. `plan` resolves the op's tier with `python -m hyper_agents.ops tier`. An unregistered op fails here.
   It runs a dry-run receipt, needs no credentials, and writes the plan to the job summary.
3. `apply-r0-r1` runs R0 and R1 applies with no environment.
4. `apply-r2` runs R2 applies in the `ops-r2` environment. The owner must approve the run in GitHub.
5. The adapter authenticates to Infisical with GitHub OIDC (identity `INFISICAL_OPS_IDENTITY_ID`),
   or with `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET` locally. GitHub writes use a PAT that
   lives in Infisical at `prod` `/ops/GITHUB_OPS_PAT` and is fetched only inside the runner.

## Tiers

| Tier | Meaning | Gate |
|---|---|---|
| R0 | read: list names, check that a secret exists | none |
| R1 | reversible write (for example a variable) | none, after dry-run |
| R2 | secret set or delete | `ops-r2` environment, owner approval |
| R3 | destructive or costly | not wired; the plan job fails on R3 |

## Registry

`ops/registry.yaml` is the only list of operations. Adding an op is a reviewed PR that adds a
registry entry and, if needed, an adapter action. Agents may propose ops by issue but cannot add
them. Secret values are never parameters. Secrets are referenced by Infisical path and name.

## Owner bootstrap (once; identity-bound, owner only)

1. **Infisical OIDC identity.** Infisical UI: Access Control → Machine Identities → the identity for
   this repo → replace Universal Auth with OIDC.
   - Discovery URL and issuer: `https://token.actions.githubusercontent.com`
   - Audience: `https://github.com/kgsmith19`
   - Subject: `repo:kgsmith19/hyper-agents:ref:refs/heads/main`. The `apply-r2` job runs in an
     environment, so its subject becomes `repo:kgsmith19/hyper-agents:environment:ops-r2`. Allow both,
     for example with a wildcard or a second identity. Verify with the first R2 run.
   - Put the identity ID in repo variable `INFISICAL_OPS_IDENTITY_ID`.
2. **Project ID.** Repo variable `INFISICAL_PROJECT_ID` (`246790d5-245f-4f4a-a730-5dda5e882b05`
   for hyperbolic-core).
3. **GitHub PAT.** Create a fine-grained PAT for `kgsmith19/hyper-agents` with repository permissions:
   Secrets (write), Variables (write), Actions (read). Store it in Infisical at `prod` `/ops/` as
   `GITHUB_OPS_PAT`. Never paste it into chat or a repo file.
4. **Environment.** GitHub → Settings → Environments → new `ops-r2`. Add yourself as a required
   reviewer. Optionally restrict deployment branches to `main`.

## D5 bounds (recorded before the first gated write)

- **Retention:** receipts live in the job summary. Git history is immutable.
- **Break-glass:** owner only, through the Infisical UI and the GitHub UI (no agent path).
- **Offline recovery:** the owner exports the Infisical `/ops/` folder and `ops/registry.yaml` to an
  encrypted offline copy.
- **Rollback for a secret set:** restore the previous Infisical version, then re-run
  `github.secret.set` from it. For a delete: re-create the secret from the offline export.

## Verify

```sh
uv run pytest tests/unit/test_ops.py -q                     # negative controls
uv run python -m hyper_agents.ops tier --op github.secret.set
uv run python -m hyper_agents.ops run --op github.secret.set \
  --params '{"repo":"kgsmith19/hyper-agents","name":"TEST_NAME","source_path":"/dev/","source_name":"DEV_OPENAI_API_KEY"}'
```

The last command is a dry-run. It prints a receipt with no values and makes no writes.

## Known limits

- The sandbox proxy blocks `/actions/secrets`, `/actions/variables` and `/environments`. Run those
  steps through the runner, not from a session.
- A dispatch from the sandbox returns 404 until `ops.yml` is on `main`. A 404 means the request
  reached GitHub.
- Desktop and local-machine ops need a self-hosted runner (P4).
