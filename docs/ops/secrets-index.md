# Secrets index (Infisical)

Where each credential lives and who should read it. **No values here.** Project: hyperbolic-core
(`246790d5-245f-4f4a-a730-5dda5e882b05`), environment `prod`. Audited 2026-11-02 with read-only
checks. `dev` and `staging` are empty.

## Rules for agents

- **One project for everything** (owner decision 2026-11-13; display name `central-vault`). Agents use CLI tools and third-party credentials from it. **No GitHub Apps.** Retired tools keep one CLI credential under `/retired/<tool>/`.

- Read a secret only through a path listed here, with the identity listed for it.
- Never print, log, or commit a value. Compare values by hash if you must.
- Prefer `/providers/<provider>/` for new code. The old per-app paths still work while consumers
  migrate. Do not delete an old path: deletion needs the owner.
- Need a credential that is not listed? Ask the owner. Do not reuse another app's key.

## Layout (applied 2026-11-13, project `central-vault`)

One Infisical project (display name `central-vault`) holds every repository's secrets. The project ID `246790d5…` and slug `hyperbolic-core-aeme2` are unchanged. Environment: `prod`. There are 55 secrets and no GitHub App keys.

| Path | Contents | Read by |
|---|---|---|
| `/` | `CLOUDFLARE_API_TOKEN`; the `AGENT_*` bundle documents | `infra/cloudflare/cf.sh`; agent-extensions bundle |
| `/providers/anthropic/` | `LLM_KEYS_ANTHROPIC`, `BRAIN_ANTHROPIC_API_KEY`, `DEV_ANTHROPIC_API_KEY`, `REVIEW_ANTHROPIC_API_KEY` | canonical copies for new code |
| `/providers/openai/` | `LLM_KEYS_OPENAI`, `DEV_OPENAI_API_KEY`, `REVIEW_OPENAI_API_KEY`, `DEEP_SEARCH_OPENAI_API_KEY` (same key as review), `OPENAI_USAGE_API_KEY` | canonical copies |
| `/providers/google/`, `/providers/kilo/`, `/providers/hetzner/` | one key each | new code; `ops/cloud/provision.sh` (Hetzner) |
| `/ops/` | `GITHUB_OPS_PAT` | `maintain.yml`, `ops.yml` |
| `/dev/` | `DEV_OPENAI_API_KEY`, `DEV_KILO_API_KEY`, `DEV_ANTHROPIC_API_KEY`, `DEV_GOOGLE_API_KEY` | `llm-conversation.yml`; kilo canary |
| `/brain/` | `BRAIN_ANTHROPIC_API_KEY` | `ops/cloud/remote.sh` |
| `/platform/core/`, `/platform/core-tunnel/`, `/platform/cloudflare/`, `/platform/backup/` | deploy SSH pair, Access service token, owner login, tunnel, TF state, restic | `ops/cloud/*.sh`, `infra/cloudflare/cf.sh` |
| `/platform/lifeos-deploy/` | `ANTHROPIC_API_KEY` (empty but read by `remote.sh`), `LIFEOS_BRIEFING_TZ`, `LIFEOS_ICS_URLS`, `LIFEOS_OWNER_USER_ID` | `ops/cloud/remote.sh` |
| `/retired/<system>/` | Retired tools and services, each commented with its old path. One CLI credential each for Tailscale (OAuth pair) and Supabase (`SUPABASE_ACCESS_TOKEN`, live); also `broker`, `llm-handler`, `shell-deploy`, `brain`, `lifeos`, `review` | nothing; kept for CLI access and recovery |

## Known problems (owner)

- **2026-11-13 reorg applied:** the 4 GitHub App keys, the App token-mint script and 16 byte-identical duplicates are deleted, unused entries moved to `/retired`, and the project renamed to `central-vault`.
- **2026-11-13 audit:** see [`infisical-audit-2026-11-13.md`](infisical-audit-2026-11-13.md). The per-secret catalog is in the Operations Registry. Most urgent:
  - `/ops/GITHUB_OPS_PAT` expires 2026-11-13 06:29 UTC and can't read admin settings. Regenerate it via #58 step 1.
  - The `ai-coding-harness` identity holds the project **admin** role. Split it per workload.

- Dead: `/platform/llm-handler/LLM_KEYS_GOOGLE` (401, intentionally unread by code), `/SUPABASE_ACCESS_TOKEN` (401). Pending deletion.
- Restricted (403): `/review/REVIEW_GOOGLE_API_KEY`, `/OPENAI_USAGE_API_KEY`.
- `/platform/llm-handler/TOOLBELT_GITHUB_INTAKE_PAT` has admin on six repos. Rotate to a narrow token.
- Duplicates: `BROKER_CALLER_TOKEN` is in both `/platform/broker/` and `/platform/llm-handler/`.
- `/AGENT_*` entries are documents, not credentials. They belong in a repo.

## LifeOS controller tokens (#82)

| Path | Names | Writer | Reader |
|---|---|---|---|
| `/lifeos/agents/` | `CLAUDE_CODE_TOKEN`, `CHATGPT_TOKEN` | `lifeos.controller_tokens.renew` (R2, ops-r2), renewed within 7 days of expiry | Claude Code sessions; the ChatGPT connector (pasted once) |
| `/platform/lifeos-signing/` | `LIFEOS_AGENT_JWT_PRIVATE_KEY` | the same op, once, when neither key half exists; never rotated | the broker only (whoever reads it can mint any token) |
| `/platform/lifeos-deploy/` | `LIFEOS_AGENT_JWT_PUBLIC_KEY` | the same op, once, when neither key half exists; never rotated | `ops/cloud/remote.sh` into the LifeOS API env |

The signing key lives apart from the tokens so that a folder-scoped privilege can deny agents access to it. Until those privileges exist, every viewer identity can still read every path: see `access-model.md`.
