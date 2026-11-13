# Infisical audit, 2026-11-13 (metadata only)

## Owner decisions (2026-11-13), which supersede parts of §4–§7

- **One project for every repository.** It is too costly to run several. Rename the display name of `hyperbolic-core` to **`central-vault`** (alternatives: `kgs-vault`, `fleet-secrets`). Keep the project ID `246790d5…` and the slug `hyperbolic-core-aeme2`: workflows embed the ID, and agent-extensions reads `INFISICAL_PROJECT_SLUG`. §4's multi-project split is withdrawn; its boundaries become top-level folders.
- **No GitHub Apps for agents.** Delete `DEV_GITHUB_APP_ID`, `DEV_GITHUB_APP_PRIVATE_KEY`, `REVIEW_GITHUB_APP_ID` and `REVIEW_GITHUB_APP_PRIVATE_KEY`. Their only reader (hyperbolic-core `auto-merge-issue-close-reconcile.yml`) has failed on every recent run. `AGENT_MINT_GITHUB_APP_TOKEN_MJS` goes once agent-extensions stops requiring it in the bundle.
- **CLI and third-party access for every tool.** A retired tool keeps one CLI credential under `/retired/<tool>/`: Tailscale (one OAuth pair) and Supabase (`SUPABASE_ACCESS_TOKEN`, live HTTP 200 on 2026-11-13). Everything without a live reader moves to `/retired/<system>/`, commented with its old path. Byte-identical copies are deleted, keeping the canonical copy.
- **Correction to §7:** `/platform/lifeos-deploy/ANTHROPIC_API_KEY` is empty but **live**: `ops/cloud/remote.sh` writes it into `lifeos.env`. It is not a deletion candidate; set a value or remove the line in `remote.sh`.
- **Status:** the reorg plan (4 App deletions, 16 duplicate deletions, 17 moves into `/retired`, rename) is prepared and dry-run clean. The live write was refused by this session's permission policy, so it has **not** been applied. Run it with Infisical write permission.

This audit contains no values. Duplicates were found by comparing 8-hex SHA-256 prefixes computed in memory. Keys are identified by public-key fingerprint. The per-secret catalog (76 rows) is in the Operations Registry → **Secrets & Access Catalog**. This file is the architecture record; `secrets-index.md` remains the agent-facing index.

## 1. Coverage

| Surface | Result |
|---|---|
| Projects visible to `ai-coding-harness` | 1: `hyperbolic-core` (`246790d5…`) |
| Environments | `dev` (0 secrets), `staging` (0), `prod` (76) |
| Folders, secrets, versions, tags, imports | read: no tags, no imports |
| Identity memberships | read (10 identities, below) |
| Integrations, secret syncs | read: none |
| Audit logs | **HTTP 400**: not available on this plan/identity, so **no last-access evidence** |
| Service tokens, dynamic secrets, rotations, roles | HTTP 404: not used or not exposed |

The organization level (other projects, groups, org roles) is not visible to this identity. **This is a gap.**

## 2. Secret → consumer graph (summary)

Matched by name and path against `main` of the four fleet repositories. Workflow fetches confirmed:

- `/review/`: review-gate OIDC (hyperbolic-core `pr-verify.yml`).
- `/dev/`: dev-agent OIDC.
- `/settings-app/`: agent-extensions `apply-github-settings.yml`. **The folder does not exist; that workflow fails closed.**
- `/ops/GITHUB_OPS_PAT`: hyper-agents `maintain.yml` and `ops.yml`, via `ai-coding-harness` OIDC.
- `/platform/*`: per-service deploy scripts (`ops/cloud/remote.sh`, `infra/cloudflare/cf.sh`, `provision.sh`).
- `/AGENT_*`: agent-extensions bundle installer.

No static consumer was found for:
- `BRAIN_DEPLOY_SSH_KEY`
- `BROKER_DEPLOY_SSH_KEY`
- `OPENAI_USAGE_API_KEY`
- `REVIEW_GOOGLE_API_KEY`
- the Supabase trio (`SUPABASE_DB_URL` ×2, `SUPABASE_SERVICE_ROLE_KEY`, `LIFEOS_SUPABASE_URL`)

That alone is not evidence of non-use: the audit log is unavailable.

## 3. Risks and redundancy

1. **`/ops/GITHUB_OPS_PAT` expires 2026-11-13 06:29 UTC.** It is a fine-grained PAT owned by `kgsmith19`, with admin on all four repos. Despite the admin permission, the live `maintain` run shows it cannot read the admin-only settings fields and gets 403 commenting in agent-engineering-standard. It is both expiring and under-scoped for its job. → owner action #58 step 1.
2. **`ai-coding-harness` holds the project `admin` role.** That one identity is shared by this coding-agent session, the `maintain` and `ops` workflows, and the ops broker. It can read and write every prod secret.
3. **13 duplicate-value groups (32 entries), all copies with no imports:**
   - `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET`, each ×5;
   - `LLM_KEYS_ANTHROPIC` ×3;
   - one OpenAI key ×4: `DEEP_SEARCH`, `REVIEW` ×2, and the root `DEEP_SEARCH`;
   - `BROKER_CALLER_TOKEN` ×2;
   - every `/providers/*` entry is mirrored in a per-app path.
4. **Environment encoded in paths.** `/dev/DEV_*` lives in `prod`, while the `dev` and `staging` environments are empty.
5. **Empty secret:** `/platform/lifeos-deploy/ANTHROPIC_API_KEY`.
6. **Documents stored as secrets:** six `/AGENT_*` entries at root.
7. **Over-scoped PAT:** `TOOLBELT_GITHUB_INTAKE_PAT`, admin on six repos (2026-11-02 index).
8. **Retired vendor:** the Supabase credentials remain, though the platform moved to local SQLite (#508/#512, #529).
9. **No rotation metadata, owner, expiry or fingerprint** is recorded on any entry.

## 4. Target architecture

- **Projects** follow security boundaries:
  - `platform-prod`: workload runtime;
  - `agents`: controller and agent capabilities;
  - `ci`: workflow identities;
  - `providers`: vendor root keys, imported, never copied.
- **Environments** are real: `dev`, `staging` and `prod`. Paths no longer carry an environment.
- **Paths:** `/<domain>/<system>/<component>`. Examples: `/platform/lifeos/api`, `/agents/lifeos/controllers`, `/ci/github/ops`.
- **Names:** `UPPER_SNAKE` with a type suffix (`_TOKEN`, `_API_KEY`, `_PRIVATE_KEY`, `_PUBLIC_KEY`, `_URL`, `_DSN`, `_CLIENT_ID`, `_CLIENT_SECRET`). The consumer is named by the path, not a prefix: `DEV_`, `BRAIN_` and `REVIEW_` go away once imports exist.
- **One canonical secret** per credential per boundary. Consumers use Infisical **secret imports or references**.
- **One machine identity per workload, least privilege.** `ai-coding-harness` is split into:
  - `agent-session` (read-only, no prod writes);
  - `ops-broker` (writes only `/ops`, `/agents`);
  - `fleet-maintain` (reads `/ci/github/ops` only).
- **Prefer OIDC and short-lived credentials.** Replace `GITHUB_OPS_PAT` with a GitHub App installation token minted per run. The pattern already exists in `apply-github-settings.yml`.
- **Agents receive capabilities, not secrets.** A broker resolves references at run time into one process. Receipts record the secret ID, version and fingerprint, never the value.

## 5. Identity / access matrix (live)

| Identity | Role | Used by |
|---|---|---|
| `ai-coding-harness` (`813dda9e…`) | **admin** | coding-agent sessions, `maintain.yml`, `ops.yml` |
| `review-gate` | viewer | hyperbolic-core review lane (`/review/`) |
| `dev-agent` | viewer | dev-agent and `llm-conversation.yml` (`/dev/`) |
| `brain-deploy`, `broker-deploy`, `llm-handler-deploy`, `shell-deploy`, `lifeos-deploy` | viewer | per-service deploys |
| `platform-backup`, `platform-restic` | viewer | backups |

Viewer roles are **project-wide**: each deploy identity can read every prod secret. Folder-scoped privileges are the next least-privilege step.

## 6. Staged migration and rotation plan

Do one credential family at a time: create the canonical entry → import it at the consumer path → cut over → capability-test → observe 7 days → retire the copy.

1. **Ops PAT (now):** the owner regenerates it, fleet-only, with Administration read+write and Issues write (#58 step 1). `maintain` verifies it. Then replace it with a GitHub App (P-43).
2. **Split `ai-coding-harness`.** Create the three identities above, move the workflow `INFISICAL_OPS_IDENTITY_ID`, then downgrade the old identity to viewer and finally remove it.
3. **Tailscale.** Create `/providers/tailscale` as canonical, import it into the 5 paths, then consider one OAuth client per workload.
4. **LLM keys.** Make `/providers/{anthropic,openai,google,kilo}` canonical and import them. Split the shared OpenAI review/deep-search key into two keys.
5. **`BROKER_CALLER_TOKEN`.** Keep one canonical entry and import it at the other path.
6. **`TOOLBELT_GITHUB_INTAKE_PAT`.** Mint a narrow token (Issues write on the intake repo only), cut over llm-handler, then revoke the old one.
7. **Environments.** Move `/dev/*` into the `dev` environment as the same names without the prefix, keeping imports for the old paths during cutover.

## 7. Deletion-candidate ledger (gated, nothing deleted)

| Secret | Why | Gate before delete |
|---|---|---|
| `/platform/lifeos-deploy/ANTHROPIC_API_KEY` | empty | confirm the lifeos-deploy job does not read it |
| `/platform/llm-handler/LLM_KEYS_GOOGLE` | 401 (2026-11-02), unread by code | re-probe 401 and 30 days of audit evidence |
| `/platform/SUPABASE_ACCESS_TOKEN` | 401; vendor retired | re-probe; owner confirms Supabase is retired |
| Supabase trio | vendor retired | export for rollback; owner confirms |
| per-app copies in §3.3 | duplicates | only after the import cutover is verified (§6) |

All deletes require:
- full coverage (the org level and audit log are currently gaps);
- no consumers;
- a tested replacement;
- rollback;
- owner approval;
- monitoring after the delete.

## 8. Continuous governance

The `maintain` job adds the following checks, metadata only:
- **Expiry forecast.** Read the fine-grained PAT expiry header, alert 14 days ahead, and raise an owner-action item. This already exists for the ops token.
- **Orphan detection.** A name with no consumer match is flagged for observation, never auto-deleted.
- **Duplicate detection.** Fingerprint groups: a new duplicate raises a finding.
- **Drift.** A new secret without owner, expiry or fingerprint metadata raises a finding.
- **Identity scope.** Any non-human identity with `admin` raises a finding.

Findings go to the owner-actions tracker (#58) as one deduplicated item each.

## SSH and App key fingerprints (public halves only)

| Secret | Public key |
|---|---|
| `/platform/core/DEPLOY_SSH_PRIVATE_KEY` | ed25519 `SHA256:NSJGwzsftL+GGxaZSmaKAOEU5QoJaZXT70MZfMjCSdA` (matches `DEPLOY_SSH_PUBLIC_KEY`) |
| `/platform/backup/STORAGEBOX_SSH_KEY` | ed25519 `SHA256:Khpf4H2vU6QvOL8fxzJjWUAcw0ry5hw10LnjEDytCAw` |
| `/platform/llm-handler/LLM_HANDLER_SSH_KEY` | ed25519 `SHA256:Di4JQwVwXJkul6mBA/fvgKy340oZdmjYsF/jYABUNMk` |
| `/platform/shell-deploy/SHELL_DEPLOY_SSH_KEY` | ed25519 `SHA256:Wnn5Q+WvaUMFXEVckKk590jmSLBFq1lK3jVRID7m94g` |
| `/platform/broker/BROKER_DEPLOY_SSH_KEY` | ed25519 `SHA256:vmaLjroYuP7RvcNR8KCb2cQaw3BYoo8a43/AaIb8tq4` |
| `/brain/BRAIN_DEPLOY_SSH_KEY` | ed25519 `SHA256:+0Ee2e5jRH7k04OML1eDvPL3nt0iGYFaxyczJLVmzZQ` |
| `/dev/DEV_GITHUB_APP_PRIVATE_KEY` | SPKI SHA-256 prefix `6NCjVOevSVB8G+Ik` |
| `/review/REVIEW_GITHUB_APP_PRIVATE_KEY` | SPKI SHA-256 prefix `hKxKIG9pT7K/71zt` |

All eight keys are distinct. Any copy found elsewhere (for example in Drive) is matched against this table before it is removed.
