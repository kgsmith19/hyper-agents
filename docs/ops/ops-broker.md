# Ops broker runbook

The ops broker lets an agent request registered secret and config operations. A GitHub Actions
workflow runs them, reads credentials from Infisical, and writes a receipt. Agents never see
secret values. Design and decision record: agent-extensions#100 (decision record comment).
Where each secret lives: [secrets-index.md](secrets-index.md).

## How a request flows

1. An agent dispatches `.github/workflows/ops.yml` with `op`, `params` (JSON) and `mode`
   (`dry-run` or `apply`). The GitHub MCP `actions_run_trigger` or `gh workflow run` both work.
2. `plan` resolves the op's tier with `python -m hyper_agents.ops tier`. An unregistered op fails here.
   It runs a dry-run receipt, needs no credentials, and writes the plan to the job summary.
3. `apply-r0-r1` runs R0 and R1 applies with no environment.
4. `apply-r2` runs R2 applies in the `ops-r2` environment. The environment scopes the broker's
   Infisical OIDC identity (subject `environment:ops-r2`) and restricts runs to `main`. It has **no
   required reviewer**: agents request R2 ops and they run without a human click (owner decision
   2026-11-13).
5. The adapter authenticates to Infisical with GitHub OIDC (identity `INFISICAL_OPS_IDENTITY_ID`, set in `ops.yml`),
   or with `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET` locally. GitHub writes use a PAT that
   lives in Infisical at `prod` `/ops/GITHUB_OPS_PAT` and is fetched only inside the runner.

## Tiers

| Tier | Meaning | Gate |
|---|---|---|
| R0 | read: list names, check that a secret exists | none |
| R1 | reversible write (for example a variable) | none, after dry-run |
| R2 | secret set or delete, settings apply | `ops-r2` environment (OIDC scope, `main` only, no reviewer) |
| R3 | destructive or costly | not wired; the plan job fails on R3 |

## Registry

`ops/registry.yaml` is the only list of operations. Adding an op is a reviewed PR that adds a
registry entry and, if needed, an adapter action. Agents may propose ops by issue but cannot add
them. Secret values are never parameters. Secrets are referenced by Infisical path and name.

| Op | Tier | What it does |
|---|---|---|
| `infisical.secret.exists`, `infisical.secret.list_names` | R0 | Fingerprint or names only |
| `github.secret.list`, `github.variable.list`, `github.settings.get` | R0 | Names, or a fixed settings field selection |
| `vendor.key.validate` | R0 | One `GET /v1/models` to a fixed OpenAI or Anthropic host; status, validity and fingerprint only |
| `cli.run` | R0 | One argv from `CLI_ALLOWLIST` in `adapters.py`; params name a key, never argv |
| `github.variable.set`, `github.variable.delete` | R1 | Non-secret configuration |
| `infisical.access.report` | R0 | Machine identities with their role slugs (names and ids only) |
| `infisical.identity.set_role` | R2 | Set one identity to a built-in non-admin role (`member`, `viewer`, `no-access`), then read it back; admin is refused |
| `infisical.signing_folder.report` | R0 | Names, IDs and native folder-role metadata only; no secret value |
| `infisical.signing_folder.restrict` | R2 | Give one non-broker identity the permanent `lifeos-signing-deny` privilege (forbid `readValue` on `/platform/lifeos-signing`), then read it back |
| `repo.report`, `repo.settings_drift` | R0 | Fleet maintenance reads (`ops/fleet.yaml`) |
| `repo.close_sweep`, `repo.change_health` | R1 | Close Items named by merged changes; flag conflicted changes |
| `repo.ledger_drift` | R0 | Branches outside `ops/branch_ledger.yaml` that no open change uses (candidates to reconcile); deletes nothing |
| `repo.branch_prune` | R1 | Delete `ops/branch_ledger.yaml` branches, each only while its head equals the recorded SHA (tag `archive/<branch>` first where listed); never the default branch or one an open change uses; `blocked` without Contents write |
| `github.secret.set`, `github.secret.delete` | R2 | Runs in the `ops-r2` environment (no reviewer) |
| `repo.settings_apply` | R2 | Runs in the `ops-r2` environment (no reviewer) |
| `queue.history_check` | R0 | Runs the Standard's own `work_queue.py history --check` and `handoff show` (fetched at `ops/fleet.yaml` `queue.ref`); names the first missing event by sequence and fingerprint |
| `queue.history_recover` | R2 | `history-repair --apply`: append only the exact signed copy of a missing event (hash, sequence and both neighbours must match), then read history and handoff back; never fabricates, edits or deletes. Resolver of owner action `queue-history` (MB-012) |

## Identities and bootstrap

The Infisical plan allows 5 identities and the org holds 10, so no identity can be created. The
broker reuses existing identities (owner decision, 2026-11-01, agent-extensions#100). The IDs are
not secrets. They are committed in the workflows, so changing them is a reviewed PR.

| Identity | Project role | OIDC subject (hyper-agents part) | Used by |
|---|---|---|---|
| `ai-coding-harness` (`813dda9e-a986-4448-aa6f-f46e0365deca`) | admin | `repo:kgsmith19@64936641/hyper-agents@1435147762:{ref:refs/heads/main,environment:ops-r2}`, TTL 3600 | `ops.yml` apply jobs (`INFISICAL_OPS_IDENTITY_ID`) |
| `dev-agent` (`3801b514-b70d-45cb-99d2-221f9a9ceb2c`) | viewer | `…/hyper-agents@1435147762:ref:refs/heads/main`, added to its hyperbolic-core subjects | `llm-conversation.yml` (`INFISICAL_IDENTITY_ID`), `maintain.yml` (`INFISICAL_OPS_IDENTITY_ID`, read-only; see `access-model.md`) |

Audience for both: `https://github.com/kgsmith19`. The project is hyperbolic-core
(`246790d5-245f-4f4a-a730-5dda5e882b05`). If the plan is upgraded, split these into dedicated
`hyper-agents-ops` (viewer) and `ops-agent` (admin) identities and change only the IDs here.

Owner-only steps. The sandbox proxy blocks these APIs, and a PAT can only be created by a person.

1. **GitHub PAT.** Create a fine-grained PAT for `kgsmith19/hyper-agents` with repository permissions:
   Secrets (write), Variables (write), Actions (read). Store it in Infisical at `prod` `/ops/` as
   `GITHUB_OPS_PAT`. Never paste it into chat or a repo file.
2. **Environment.** GitHub → Settings → Environments → `ops-r2`. Restrict deployment branches to
   `main`. Do **not** add a required reviewer: R2 ops run without a human click (owner decision
   2026-11-13). Keep the environment itself, because the ops identity's OIDC subject names it.

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
- Desktop and local-machine ops need a self-hosted runner. `cli.run` covers the fixed
  allowlist on the GitHub-hosted runner only.
- Infisical secret rotation (a write) is not registered yet: it needs the `/ops/` folder and an
  R2 dry-run proof first.
