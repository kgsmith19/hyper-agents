# ADR 0001: Custom orchestration over the OpenHands Software Agent SDK

- Status: accepted (P01, 2026-10-29)
- Decision owner: kgsmith19. This repository is the sole canonical source of its roles; nothing is
  inherited from the Agent Engineering Standard.

## Context

Hyper-Agents must own its role contracts, team selection, identity, workflow transitions, run ledger
and evaluation. It needs a dependable LLM/tool loop, sandboxed workspaces and typed execution events,
but must not rebuild them. Deployment is local-only: one trusted Linux host, Docker workspaces, and
SQLite WAL with one controller. This follows the owner's 2026-10-29 local-only decision.

## Alternatives

| | (A) Custom orchestration + OpenHands SDK | (B) OpenHands beta automation | (C) Wrap TAKT |
| --- | --- | --- | --- |
| **What we own** | Roles, controller, ledger, selection, gates. The SDK is behind `ExecutorPort` | Their dispatcher/scheduler owns flow; we configure it | TAKT's facets and workflow YAML own flow; we extend it |
| **Duplication** | Low. We reuse the SDK's loop, delegation, workspaces and events. We write only what the SDK does not offer (authority, ledger, gates) | Low code, but its queue would duplicate GitHub Issues as a second tracker | Medium. Role authority still has to be enforced outside TAKT's prompts |
| **Release maturity** | `openhands-sdk` on PyPI: 1.59.0 (2026-10-28), 1.58.2 (2026-10-25), with frequent releases (verified 2026-10-29) | Beta, and SQLite mode is single-process only (Prompt 04 source note); not a hard dependency | A separate tool with its own lifecycle; unverified here |
| **Security** | Hard controls (grants, mounts, network, credentials) stay ours and outside prompts. The SDK's analyzer is defense-in-depth only | Its trust model would be ours by default | Prompt and facet based; enforcement must be added anyway |
| **Integration cost** | One adapter (`OpenHandsExecutor`) plus a compatibility probe | Low at first, high to bend toward owned roles | Medium to high: a TS/CLI boundary per run |
| **Licensing** | To verify at the P03 pin: the PyPI metadata for `openhands-sdk` 1.59.0 carries no license field. Record the upstream repository's license file at the pinned tag before adding the dependency | Same as A | To verify before any reuse |

## Decision

**(A).** The controller is deterministic application code, not a fifth LLM persona. Workers run through
`ExecutorPort`, implemented by `OpenHandsExecutor` from P03. P03 must use these verified imports (found
in `openhands_sdk-1.59.0`, `openhands/sdk/subagent/registry.py`): `register_agent`,
`agent_definition_to_factory`, `get_agent_factory` and `AgentFactory`. `DockerWorkspace` lives in the
separate `openhands-workspace` package (1.59.0 on PyPI). P03 re-verifies its import path at the pin.

What we adapt as ideas, without copying code:
- TAKT's facet separation;
- Argus's distinct role authority and read-only Reviewer;
- roborev's background review and findings ledger;
- Symphony's lease, retry and reconcile model.

## Pins

- `openhands-sdk`: candidate **1.58.2**. 1.59.0 is under two weeks old; P03 moves the pin only after its
  compatibility probe passes.
- `openhands-workspace`: the same release train as the SDK.
- `uv` 0.11.32 in CI, and Python 3.12.

## Consequences

- An SDK API change breaks only the adapter, and `hyper-agents doctor` reports it. The run fails with an
  actionable error, never a fake success.
- No OpenHands automation dependency. Our queue stays GitHub Issues plus a local SQLite RunStore.
- Before P03 adds the SDK, the dependency license audit (`ci/licenses.py`) must pass with it.
