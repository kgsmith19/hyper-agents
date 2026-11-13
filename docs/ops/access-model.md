# Access model (P-50): one workload, one least-privilege identity

Status 2026-11-13. This page lists which machine identity each workload uses for Infisical
(project `central-vault`, `246790d5-…`). It replaces the plan in
`infisical-audit-2026-11-13.md` §6 step 2, which assumed new identities could be created.

## Constraint

The Infisical plan allows 5 machine identities. The org already holds 10, so **no identity can
be created** until some are consolidated. The split therefore reuses existing identities. It
narrows roles rather than adding identities.

## Target

| Workload | Needs | Identity | Role | Status |
|---|---|---|---|---|
| `maintain.yml` (every 30 min, R0/R1) | read `/ops/GITHUB_OPS_PAT`, check names under `/lifeos/agents` | `dev-agent` (`3801b514…`) | viewer | **moved off admin** (this change) |
| `ops.yml` plan job | none | none | none | unchanged |
| `ops.yml` apply jobs (R1/R2, `ops-r2`) | write `/ops`, `/agents`, read vendor keys | `ai-coding-harness` (`813dda9e…`) | admin → custom `ops-broker` role | role narrowing pending (MB-009) |
| `llm-conversation.yml` | read `/dev/` | `dev-agent` | viewer | unchanged |
| coding-agent sessions | read-only by default; writes go through the broker | `ai-coding-harness` universal auth | admin → viewer once the broker covers writes | pending (MB-009) |
| hyperbolic-core deploys, backups, review lane | per service | existing per-service identities | viewer (project-wide) | folder-scoped privileges are next |

## Why `dev-agent` for maintenance

- Its OIDC subject already trusts `kgsmith19/hyper-agents` `main`, with the same audience
  (`https://github.com/kgsmith19`) the ops adapter requests. No Infisical change is needed.
- Viewer is enough: maintenance reads one token and checks that names exist. It never writes
  a secret.
- After the switch, the scheduled job (48 runs a day) no longer holds an identity that can
  write every prod secret.

**Proof:** a maintain run on `main` after the merge succeeds, and #58 still shows step 1 "works
in all 4 repositories". **Rollback:** revert the one-line `INFISICAL_OPS_IDENTITY_ID` change
in `maintain.yml`.

## Remaining (MB-009)

1. Create a custom project role `ops-broker`: write on `/ops` and `/agents`, read on
   `/providers`. Assign it to `ai-coding-harness` in place of `admin`. This is a project-admin
   change, and this session's policy refuses secret-store and role writes. It needs Kyle, or a
   one-time allow rule, under the ManualBlocker contract.
2. Verify `ops.yml` R0, R1 and R2 runs on the narrowed role, including `github.secret.set` from
   `/ops`.
3. Move coding-agent sessions to a read-only credential (`viewer`) once every write they make
   goes through the broker.
4. Consolidate the five per-service deploy identities if the plan limit is to be met. Until
   then, add folder-scoped privileges to each.
