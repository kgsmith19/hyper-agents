# Hyper-Agents

An owner-controlled, installable multi-agent SDLC engine. A **deterministic controller** (application
code, not a language model) admits a task with an executable acceptance contract, selects only the
specialists the task needs, **mints** a fresh, sandboxed worker per assignment, and **discharges** it
with a receipt once its evidence is signed off.

- **Four owned roles:** Planner, Builder, Reviewer, Verifier. This repository is their only canonical
  source.
- **Execution:** the [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk)
  behind a narrow executor port, in Docker workspaces.
- **Runs locally:** SQLite WAL with a single controller process. The daemon is persistent; role
  instances are ephemeral.

Status: **P01 (foundation)**. Nothing here runs a real agent yet; `hyper-agents doctor` says so.

## Quick start

```sh
uv sync
uv run hyper-agents version
uv run hyper-agents doctor
uv run pytest
```

## Documents

- [`docs/adr/0001-orchestration.md`](docs/adr/0001-orchestration.md): why custom orchestration over the
  OpenHands SDK.
- [`docs/design/v1.md`](docs/design/v1.md): the V1 design (roles, events, errors, leases, secrets,
  budgets, non-goals).
- [`docs/plan/implementation-plan.md`](docs/plan/implementation-plan.md): P02–P07, with files,
  interfaces and tests.
- [`docs/issues/manifest.json`](docs/issues/manifest.json): the thin-Issue plan in dependency order.
- [`AGENTS.md`](AGENTS.md): how to work in this repository.
