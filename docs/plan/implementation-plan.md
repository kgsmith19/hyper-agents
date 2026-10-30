# Implementation plan: P02 → P07

Each stage starts only after the previous stage is green on `main`. Each Issue keeps to five claims or
fewer. Paths are relative to the repository root.

## P02: owned role catalog and compiler

| Files | Interfaces | Tests |
| --- | --- | --- |
| `agents/catalog/{planner,builder,reviewer,verifier}/{role.yaml,persona.md,instructions.md,output.schema.json}` | — | `tests/conformance/test_catalog_snapshot.py`: repeated loads compile byte-identically |
| `src/hyper_agents/domain/roles.py` | `RoleManifest` (pydantic strict: id, version, capabilities, tool allowlist, permitted paths, prohibited writes, network policy, sandbox profile, provider tier, limits, approval thresholds, IO schemas, evidence, dependencies) | Unknown roles, version drift, inherited elevated tools, path or symlink escapes and forged manifests are all rejected |
| `src/hyper_agents/application/catalog.py` | `CatalogLoader.load()`, `CatalogValidator.validate()`, `RoleRegistry.list()` | YAML is loaded with `safe_load` only, and a tagged-YAML payload is refused |
| `src/hyper_agents/application/compiler.py` | `RoleCompiler.compile(role, task, grants)`, with precedence owner task > acceptance > role instructions > knowledge | A malicious persona asking for writes is still denied by its grant |
| `src/hyper_agents/interfaces/cli.py` | `roles list/show/validate` | CLI integration tests |

## P03: executor, mint/discharge, sandboxed workers

- `application/ports.py` gets `ExecutorPort`.
- `adapters/openhands_executor.py` implements it.
- `adapters/fake_executor.py` provides offline runs.
- `application/factory.py` provides `AgentFactory.mint(MintRequest) -> Lease` and
  `discharge(Lease) -> Receipt`.
- Docker hardening: a non-root UID, dropped capabilities, read-only mounts for the Reviewer, and
  pids, memory and CPU limits.
- A per-role network deny-by-default policy and a host-side tool policy gateway.
- CLI: `doctor`, `mint`, `status`, `cancel`.
- Tests:
  - idempotent mint;
  - parallel worker isolation;
  - forbidden writes and symlink escapes;
  - cancellation and orphan cleanup;
  - secret redaction;
  - an SDK compatibility probe that fails closed.

## P04: workflow, team selection, recovery

- `workflows/default.yaml` is a strictly validated state machine.
- `application/controller.py` owns transitions, approvals and retries.
- `application/team.py` provides `TeamSelector`, choosing by risk R0–R3 and by path, schema and
  authorization impact.
- `adapters/sqlite_run_store.py` uses WAL, leases and heartbeats, backoff with jitter, budgets and
  checkpoints.
- `adapters/github_tracker.py` is read-only polling.
- Fault-injection tests cover:
  - simultaneous claims, a lost lease, and a crash during dispatch;
  - a duplicate webhook and a stale SHA;
  - an illegal transition and malformed output;
  - budget exhaustion and restart recovery.

## P05: independent review and provable completion

- `application/review.py` provides `ReviewService.review(...)`, returning a `ReviewVerdict` whose
  findings have stable IDs.
- `application/verify.py` provides `VerificationService.verify(sha, criteria)`, returning a hashed
  `VerificationReceipt`.
- A deduplicated findings ledger.
- Security tests:
  - a read-only Reviewer cannot mutate;
  - a prompt cannot relax OS policy;
  - a revoked lease cannot publish;
  - a stale SHA is never green.
- The PR Gate grows lanes for these.

## P06: entrypoints and local hosting

- One application path: CLI → API → service → `ExecutorPort`. Every other entrypoint adapts to it:
  - an interactive `call`;
  - a headless JSON CLI;
  - a signed webhook;
  - a local daemon on the RunStore;
  - a remote OpenHands Agent Server adapter.
- JSON Schemas for `RunRequest`, `RunStatus`, `Event`, `Evidence` and `CancelRequest`.
- Docker Compose for a single local host.
- A reversible hook installer.

## P07: evaluation and V1 release

- A frozen held-out fixture suite with trusted oracles, comparing four workflow variants on equal
  budgets.
- Adversarial probes.
- An SBOM, a license audit and pinned provenance.
- A runbook, then a v1.0.0 tag at the exact green SHA.
