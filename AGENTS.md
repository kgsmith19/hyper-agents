# Working in hyper-agents

These are the rules for humans and coding agents working in this repository. The owner (`kgsmith19`)
may override any of them. When the owner overrides one, record the decision on the Issue it affects.

## Sources of truth

| What | Where |
| --- | --- |
| Work intent and acceptance | GitHub Issues: one observable outcome each, at most five claims |
| Product design | `docs/design/v1.md`, plus ADRs in `docs/adr/` |
| Roles, personas and policies | `agents/catalog/` (from P02). This repo is their only source; nothing is inherited from the Agent Engineering Standard |
| Exact commands | `pyproject.toml` and this file |

## Commands

```sh
uv sync                                   # install
uv run ruff format --check . && uv run ruff check .
uv run pyright
uv run pytest                             # unit, integration, security, conformance
```

## Rules

- **Layers:** `domain` ← `application` ← `adapters` / `interfaces`. The domain imports only the
  standard library and pydantic, and `tests/conformance` enforces this.
- **Prompts are advisory.** Hard controls (paths, network, credentials, approvals) are structured grants
  enforced outside the model.
- **Secrets:** never in the repository, YAML, logs, images or Issues. Local `.env` files are gitignored.
- **Tests:** red before green. Each test must be able to fail; include a negative control when adding a
  rule.
- **PRs:** one per Issue, ready (not draft), `Closes #N`. The single required check is `PR Gate`. The
  owner merges; an agent never merges and never bypasses the gate.
