# Secrets index (Infisical)

Where each credential lives and who should read it. **No values here.** Project: hyperbolic-core
(`246790d5-245f-4f4a-a730-5dda5e882b05`), environment `prod`. Audited 2026-11-02 with read-only
checks. `dev` and `staging` are empty.

## Rules for agents

- Read a secret only through a path listed here, with the identity listed for it.
- Never print, log, or commit a value. Compare values by hash if you must.
- Prefer `/providers/<provider>/` for new code. The old per-app paths still work while consumers
  migrate. Do not delete an old path: deletion needs the owner.
- Need a credential that is not listed? Ask the owner. Do not reuse another app's key.

## Layout

| Path | Contents | Read by |
|---|---|---|
| `/providers/anthropic/` | `LLM_KEYS_ANTHROPIC`, `BRAIN_ANTHROPIC_API_KEY`, `DEV_ANTHROPIC_API_KEY`, `REVIEW_ANTHROPIC_API_KEY` | new code; name says the consumer |
| `/providers/openai/` | `LLM_KEYS_OPENAI`, `DEV_OPENAI_API_KEY`, `REVIEW_OPENAI_API_KEY`, `DEEP_SEARCH_OPENAI_API_KEY` | new code. The last two are the same key. |
| `/providers/google/` | `DEV_GOOGLE_API_KEY` | new code |
| `/providers/kilo/` | `DEV_KILO_API_KEY` | new code |
| `/providers/hetzner/` | `HETZNER_API_TOKEN` | backups |
| `/ops/` | `GITHUB_OPS_PAT` | ops broker (`ops.yml`, `ai-coding-harness` OIDC). Needs Secrets/Variables/Actions permissions added by the owner. |
| `/dev/` | dev-agent keys and GitHub App | `dev-agent-dispatch.yml`, `llm-conversation.yml` (dev-agent OIDC) |
| `/review/` | review keys and GitHub App | `llm-review*.yml`, `verify-llm-review` |
| `/brain/` | brain keys, deploy SSH, Tailscale | brain service |
| `/platform/broker/`, `/platform/llm-handler/` | service credentials | `services/broker`, `services/llm-handler` |
| `/platform/backup/`, `/platform/db-backup/`, `/platform/lifeos-deploy/`, `/platform/shell-deploy/` | backup and deploy values | backup and deploy jobs |
| `/github/`, `/ssh/`, `/tailscale/`, `/deploy/`, `/config/` | empty, reserved for the migration | none yet |

## Known problems (owner)

- Dead: `/platform/llm-handler/LLM_KEYS_GOOGLE` (401, intentionally unread by code), `/SUPABASE_ACCESS_TOKEN` (401). Pending deletion.
- Restricted (403): `/review/REVIEW_GOOGLE_API_KEY`, `/OPENAI_USAGE_API_KEY`.
- `/platform/llm-handler/TOOLBELT_GITHUB_INTAKE_PAT` has admin on six repos. Rotate to a narrow token.
- Duplicates: `BROKER_CALLER_TOKEN` is in both `/platform/broker/` and `/platform/llm-handler/`.
- `/AGENT_*` entries are documents, not credentials. They belong in a repo.
