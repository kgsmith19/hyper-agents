# Branch reconciliation, 2026-11-13

Every non-default remote branch in the four fleet repositories, with one disposition each. The disposition comes from live evidence: a merged PR whose head equals the branch head, ancestry of `main`, or file-content equality on `main`. A recount of `ahead` commits alone misleads here, because the repos squash-merge and hyperbolic-core `main` was rewritten (9 old branches share no merge base with it).

- **MERGED_OR_CONTAINED**: delete, using the head SHA as a lease.
- **SUPERSEDED_OR_ABANDONED**: tag `archive/<branch>` at the exact SHA, then delete. The owner chose this on 2026-11-13.

Retained: `ccr-75b2b0cd-yfz3b3` in each repository is the active session branch.

Blocker: session pushes can only reach the session branch, and the connected GitHub tools have no ref-delete. So the deletion below is one owner command (`scripts/prune-branches.sh`, read-verify-then-delete). Follow-up automation: a lease-checked `repo.branch_prune` op in `maintain`, plus `delete_branch_on_merge` via settings apply, so branches stop piling up (#65).

| Repository | Branch | Head SHA | Disposition | Evidence | Action |
|---|---|---|---|---|---|
| hyperbolic-core | `ccr-3fd10670-dj781f` | `d38da4609488` | MERGED_OR_CONTAINED | merged PR #524 head | delete (lease) |
| hyperbolic-core | `ccr-70fd9e88-v8oqpz` | `24639a8db7ef` | MERGED_OR_CONTAINED | merged PR #536 head | delete (lease) |
| hyperbolic-core | `ccr-a3588a7e-gkdp4s` | `894862d4cf63` | MERGED_OR_CONTAINED | merged PR #622 head | delete (lease) |
| hyperbolic-core | `ccr-b18954ce-o770ss` | `3c780aa83465` | MERGED_OR_CONTAINED | merged PR #564 head | delete (lease) |
| hyperbolic-core | `claude/sleepy-lamport-rk45pp` | `b83370dc7325` | MERGED_OR_CONTAINED | pre-rewrite main squash of #447 (merged) | delete (lease) |
| hyperbolic-core | `feat/lifeos-535-identity-crosswalk` | `6a8a6ef89c8d` | MERGED_OR_CONTAINED | merged PR #570 head | delete (lease) |
| hyperbolic-core | `feat/lifeos-576-today-calm-ui` | `5670ea123572` | MERGED_OR_CONTAINED | merged PR #577 head | delete (lease) |
| hyperbolic-core | `issue/391-stage63-adopt-v5` | `23b9017056be` | MERGED_OR_CONTAINED | pre-rewrite main squash of #416 (merged) | delete (lease) |
| hyperbolic-core | `issue/421-s3-verification-molds` | `abc805ac3cb5` | MERGED_OR_CONTAINED | merged PR #431 head | delete (lease) |
| hyperbolic-core | `issue/422-implementation-quality-semantics` | `9868a4330521` | MERGED_OR_CONTAINED | merged PR #435 head | delete (lease) |
| hyperbolic-core | `issue/433-lifeos-writer-preflight` | `199de7b11bcd` | MERGED_OR_CONTAINED | merged PR #434 head | delete (lease) |
| hyperbolic-core | `issue/436-agent-app-identities-and-prompt-close` | `d6031671565d` | MERGED_OR_CONTAINED | merged PR #437 head | delete (lease) |
| hyperbolic-core | `issue/440-reviewer-mint-contents-read` | `9d37453d623c` | MERGED_OR_CONTAINED | merged PR #452 head | delete (lease) |
| hyperbolic-core | `issue/445-queue-guard` | `165f3a167462` | MERGED_OR_CONTAINED | merged PR #447 head | delete (lease) |
| hyperbolic-core | `issue/450-monorepo-closeout` | `aeb69cfbbf19` | MERGED_OR_CONTAINED | merged PR #540 head | delete (lease) |
| hyperbolic-core | `issue/506-sqlite-kit` | `f2402ff935bd` | MERGED_OR_CONTAINED | packages/sqlite-kit identical on main (#507 closed) | delete (lease) |
| hyperbolic-core | `issue/510-repoint-platform` | `56e8e29d3427` | MERGED_OR_CONTAINED | merged PR #511 head | delete (lease) |
| hyperbolic-core | `issue/531-today-command-center` | `5b025ed02fc1` | MERGED_OR_CONTAINED | merged PR #544 head | delete (lease) |
| hyperbolic-core | `issue/531-today-day-context` | `a764cbcf8cf9` | MERGED_OR_CONTAINED | merged PR #550 head | delete (lease) |
| hyperbolic-core | `issue/547-remove-ai-review` | `0780037cfc2d` | MERGED_OR_CONTAINED | merged PR #548 head | delete (lease) |
| hyperbolic-core | `issue/551-consolidate-apps` | `842220ce1cd9` | MERGED_OR_CONTAINED | merged PR #552 head | delete (lease) |
| hyperbolic-core | `issue/559-local-first-activation` | `383f5b4bb80b` | MERGED_OR_CONTAINED | same SHA as merged PR #561 head | delete (lease) |
| hyperbolic-core | `issue/560-local-readiness` | `383f5b4bb80b` | MERGED_OR_CONTAINED | merged PR #561 head | delete (lease) |
| hyperbolic-core | `issue/588-sync-receipts` | `1360da556126` | MERGED_OR_CONTAINED | merged PR #597 head | delete (lease) |
| hyperbolic-core | `issue/589-sync-rollback` | `9933763dc787` | MERGED_OR_CONTAINED | ancestor of main | delete (lease) |
| hyperbolic-core | `audit/lifeos-consolidation-20261022` | `002dcb8a4d66` | SUPERSEDED_OR_ABANDONED | PR #432 closed unmerged; point-in-time audit superseded | tag archive/, then delete (lease) |
| hyperbolic-core | `fix/lifeos-569-design-tokens` | `2b4278e87f6e` | SUPERSEDED_OR_ABANDONED | PR #579 closed; superseded by merged #581 | tag archive/, then delete (lease) |
| hyperbolic-core | `issue/443-queue-guard-proof` | `001c7953c9f6` | SUPERSEDED_OR_ABANDONED | DO-NOT-MERGE proof PR #446 (completed) | tag archive/, then delete (lease) |
| hyperbolic-core | `issue/499-freeze-cloud-ops` | `407f65b32883` | SUPERSEDED_OR_ABANDONED | PR #500 closed; superseded by merged #505 | tag archive/, then delete (lease) |
| hyper-agents | `ccr-1b50d880-9z1eh8` | `9a83d98edb2f` | MERGED_OR_CONTAINED | merged PR #20 head | delete (lease) |
| hyper-agents | `ccr-70fd9e88-v8oqpz` | `fd718bacfa2d` | MERGED_OR_CONTAINED | merged PR #18 head | delete (lease) |
| hyper-agents | `ccr-81dd9f1d-7hlvo3` | `0905c2563c90` | MERGED_OR_CONTAINED | merged PR #15 head | delete (lease) |
| hyper-agents | `ccr-b18954ce-o770ss` | `35e62b2294ae` | MERGED_OR_CONTAINED | merged PR #52 head | delete (lease) |
| hyper-agents | `fix/idempotent-secret-delete` | `a838fabdacf6` | MERGED_OR_CONTAINED | merged PR #53 head | delete (lease) |
| hyper-agents | `issue/100-ops-broker-p2-p4` | `faf0e5a75d0d` | MERGED_OR_CONTAINED | merged PR #24 head | delete (lease) |
| hyper-agents | `issue/21-aes-conformance` | `bcfe8d7494c6` | MERGED_OR_CONTAINED | merged PR #22 head | delete (lease) |
| hyper-agents | `issue/25-team-roles` | `f124bc815c27` | MERGED_OR_CONTAINED | merged PR #26 head | delete (lease) |
| hyper-agents | `issue/27-workflow-team` | `0f2ec05f530f` | MERGED_OR_CONTAINED | merged PR #28 head | delete (lease) |
| hyper-agents | `issue/29-sqlite-run-store` | `c8832f5804a6` | MERGED_OR_CONTAINED | merged PR #30 head | delete (lease) |
| hyper-agents | `issue/31-controller-tracker` | `144a4d25751b` | MERGED_OR_CONTAINED | merged PR #32 head | delete (lease) |
| hyper-agents | `issue/33-p05-review-verify` | `01b7fe61e42d` | MERGED_OR_CONTAINED | merged PR #34 head | delete (lease) |
| hyper-agents | `issue/35-api-contracts` | `d228d2c3eed9` | MERGED_OR_CONTAINED | merged PR #36 head | delete (lease) |
| hyper-agents | `issue/37-headless-json-cli` | `c090e46f3ec6` | MERGED_OR_CONTAINED | merged PR #38 head | delete (lease) |
| hyper-agents | `issue/39-signed-webhook` | `0c1078af3649` | MERGED_OR_CONTAINED | merged PR #40 head | delete (lease) |
| hyper-agents | `issue/41-local-sqlite-daemon` | `2b8967ffda26` | MERGED_OR_CONTAINED | merged PR #42 head | delete (lease) |
| hyper-agents | `issue/43-agent-server-adapter` | `7228a5af3ea0` | MERGED_OR_CONTAINED | merged PR #44 head | delete (lease) |
| hyper-agents | `issue/45-compose-hook-installer` | `6e0476c33070` | MERGED_OR_CONTAINED | merged PR #46 head | delete (lease) |
| hyper-agents | `issue/47-frozen-eval-fixtures` | `3eea7afc8076` | MERGED_OR_CONTAINED | merged PR #48 head | delete (lease) |
| hyper-agents | `issue/49-adversarial-probes` | `1d5cdfec1cc3` | MERGED_OR_CONTAINED | merged PR #50 head | delete (lease) |
| hyper-agents | `ops/broker-p1` | `bd6c26f49682` | MERGED_OR_CONTAINED | merged PR #16 head | delete (lease) |
| hyper-agents | `ccr-9b07541b-53w1e0` | `a317fb5bbeff` | SUPERSEDED_OR_ABANDONED | PR #14 closed; superseded by merged #15 | tag archive/, then delete (lease) |
| hyper-agents | `ccr-d9c33ca1-119wwp` | `4f0726ec9447` | SUPERSEDED_OR_ABANDONED | no PR; P03-2 superseded by merged #15 | tag archive/, then delete (lease) |
| hyper-agents | `issue/19-docker-policy` | `02c903f07c4e` | SUPERSEDED_OR_ABANDONED | no PR; superseded by merged #20 sandbox gateway | tag archive/, then delete (lease) |
| agent-engineering-standard | `ccr-1b50d880-9z1eh8` | `4a72f88d3db9` | MERGED_OR_CONTAINED | same SHA as merged PR #326 head | delete (lease) |
| agent-engineering-standard | `issue/324-review-contract` | `4a72f88d3db9` | MERGED_OR_CONTAINED | merged PR #326 head | delete (lease) |
| agent-engineering-standard | `ccr-a3588a7e-gkdp4s` | `1d97dc281624` | MERGED_OR_CONTAINED | merged PR #337 head | delete (lease) |
| agent-engineering-standard | `ccr-d113909a-3mabiw` | `e58c9b5865bf` | MERGED_OR_CONTAINED | ancestor of main | delete (lease) |
| agent-engineering-standard | `claude/sleepy-lamport-rk45pp` | `3c0e4ce026e7` | MERGED_OR_CONTAINED | ancestor of main | delete (lease) |
| agent-engineering-standard | `issue/279-session-start-adapter` | `a4579cd86d1e` | MERGED_OR_CONTAINED | merged PR #280 head | delete (lease) |
| agent-engineering-standard | `issue/287-queue-guard` | `e0eedd761c07` | MERGED_OR_CONTAINED | merged PR #291 head | delete (lease) |
| agent-engineering-standard | `issue/321-aes-vnext-conformance` | `0761a02c215e` | MERGED_OR_CONTAINED | merged PR #323 head | delete (lease) |
| agent-engineering-standard | `issue/327-conformance-job-check` | `7a8bc3cea0c9` | MERGED_OR_CONTAINED | merged PR #328 head | delete (lease) |
| agent-engineering-standard | `issue/329-remove-ai-review` | `542234328192` | MERGED_OR_CONTAINED | merged PR #330 head | delete (lease) |
| agent-engineering-standard | `issue/281-queue-guard-proof` | `482db9c7228f` | SUPERSEDED_OR_ABANDONED | DO-NOT-MERGE proof PR #292 (completed) | tag archive/, then delete (lease) |
| agent-extensions | `claude/sleepy-lamport-rk45pp` | `8e1acaf1c50c` | MERGED_OR_CONTAINED | ancestor of main | delete (lease) |
| agent-extensions | `issue/36-queue-guard-proof` | `10b0bcdabe3d` | SUPERSEDED_OR_ABANDONED | DO-NOT-MERGE proof PR #75 (completed) | tag archive/, then delete (lease) |
| hyper-agents | `ccr-a3588a7e-gkdp4s` | `9e7fd0f9ad52` | MERGED_OR_CONTAINED | PR #59 closed; commit contained in merged #60 | delete (lease) |
