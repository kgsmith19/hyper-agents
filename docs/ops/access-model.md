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

## Automated enforcement (MB-009)

No workload identity may hold the project `admin` role. Owner action
`least-privilege-identities` checks this on every maintenance run, through
`InfisicalAdapter.identity_roles()`. For each admin it finds, its resolver dispatches
`infisical.identity.set_role`:

- The op is R2 and runs in `ops-r2` with no reviewer.
- It replaces that identity's roles with the built-in `member` role, then reads the role back.
- It never grants `admin`, whatever the params say.
- `member` can still read and write secrets, so the broker's R1/R2 ops keep working.
- Custom roles are not used: the plan's roles endpoint returned 404.

The op only ever *removes* admin. It refuses a target that does not hold admin now, so it cannot lock
out an ordinary identity.

**This is one-shot by design.** The broker identity demotes itself. After that, no agent identity can
edit roles. A later admin grant is still **detected** on every run, but the automated fix fails
(HTTP 403) twice and then hands the one UI step to Kyle. That is the intended end state: no workload
can grant or remove admin. When the broker demotes itself, the read-back may lose permission. The op
then reports `unverified`, and the next maintenance check (run as `dev-agent`) confirms the result.

If the fix fails twice in a week, the owner checklist shows the one UI step. **Break-glass:**
Kyle's own org-admin login can reassign any role. No agent identity can grant admin.

Still open:
- Coding sessions use `ai-coding-harness` universal auth, so after this change they get `member`.
- The five per-service deploy identities stay `viewer` at the project level. #86/#88 give every
  workload except `ai-coding-harness` (the controller-token broker) one permanent identity
  additional privilege, slug `lifeos-signing-deny`, whose inverted rules forbid `readValue` on
  `prod` at `/platform/lifeos-signing` and below. Infisical permissions are additive, so only a
  forbid rule can narrow a broader role (an extra "List" grant cannot). Names and metadata remain
  visible; the private-key value does not. The R0 check is `infisical.signing_folder.report`;
  drift resolves through the R2 `infisical.signing_folder.restrict` op in `ops-r2`.
  Rollback: delete the `lifeos-signing-deny` privilege
  (`DELETE /api/v2/identity-project-additional-privilege/{id}`).

## Signing-folder boundary (#86)

**Consumer map:** `dev-agent` serves maintenance and LLM conversation; the per-service identities
serve deploy/backup workloads; `ai-coding-harness` alone runs `lifeos.controller_tokens.renew` and
therefore must read the private signing key. No credential is moved, renamed, deleted or rotated.

**Rollback:** use the recorded folder-grant ID to delete only the grant created by #86, or restore
that exact identity's previous folder role. The resolver never performs rollback automatically and
never changes the broker.
