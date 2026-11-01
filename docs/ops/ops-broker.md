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
5. The adapter authenticates to Infisical with GitHub OIDC (identity `INFISICAL_OPS_IDENTITY_ID`, set in `ops.yml`),
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

## Identities and bootstrap

The Infisical plan allows 5 identities and the org holds 10, so no identity can be created. The
broker reuses existing identities (owner decision, 2026-11-01, agent-extensions#100). The IDs are
not secrets. They are committed in the workflows, so changing them is a reviewed PR.

| Identity | Project role | OIDC subject (hyper-agents part) | Used by |
|---|---|---|---|
| `ai-coding-harness` (`813dda9e-a986-4448-aa6f-f46e0365deca`) | admin | `repo:kgsmith19@64936641/hyper-agents@1435147762:{ref:refs/heads/main,environment:ops-r2}`, TTL 3600 | `ops.yml` apply jobs (`INFISICAL_OPS_IDENTITY_ID`) |
| `dev-agent` (`3801b514-b70d-45cb-99d2-221f9a9ceb2c`) | viewer | `…/hyper-agents@1435147762:ref:refs/heads/main`, added to its hyperbolic-core subjects | `llm-conversation.yml` (`INFISICAL_IDENTITY_ID`) |

Audience for both: `https://github.com/kgsmith19`. The project is hyperbolic-core
(`246790d5-245f-4f4a-a730-5dda5e882b05`). If the plan is upgraded, split these into dedicated
`hyper-agents-ops` (viewer) and `ops-agent` (admin) identities and change only the IDs here.

Owner-only steps. The sandbox proxy blocks these APIs, and a PAT can only be created by a person.

1. **GitHub PAT.** Create a fine-grained PAT for `kgsmith19/hyper-agents` with repository permissions:
   Secrets (write), Variables (write), Actions (read). Store it in Infisical at `prod` `/ops/` as
   `GITHUB_OPS_PAT`. Never paste it into chat or a repo file.
2. **Environment.** GitHub → Settings → Environments → new `ops-r2`. Add yourself as a required
   reviewer. Restrict deployment branches to `main`.

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
