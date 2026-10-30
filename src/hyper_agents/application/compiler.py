"""Compile a role with its task context and grants (P02-4).

Precedence: task owner rules > acceptance > role instructions > knowledge.
Grants are hard controls as structured tuples, never string rewrite.
The compiled role is JSON-dumpable for transport to the executor.
"""

import json
from dataclasses import asdict, dataclass
from typing import Any

from hyper_agents.application.catalog import LoadedRole
from hyper_agents.domain.roles import RoleManifest


@dataclass(frozen=True)
class GrantLimit:
    """A hard control on token/file/time limits. Immutable, no templating."""

    max_turns: int
    timeout_seconds: int
    max_tokens: int


@dataclass(frozen=True)
class GrantApproval:
    """Hard controls on approval thresholds. Immutable, no templating."""

    max_files_changed: int
    max_lines_changed: int


@dataclass(frozen=True)
class Grant:
    """A structured grant tuple: no string rewrite, no templating."""

    limit: GrantLimit
    approval: GrantApproval
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    permitted_paths: tuple[str, ...]
    prohibited_writes: tuple[str, ...]
    network: str
    sandbox: str


@dataclass(frozen=True)
class CompiledRole:
    """A role compiled with its task and grants. JSON-dumpable."""

    role_id: str
    role_version: str
    role_summary: str
    # Owner task context
    task_owner_rules: dict[str, Any]
    # Acceptance criteria from the task
    acceptance: dict[str, Any]
    # Role instructions and persona context
    role_instructions: dict[str, Any]
    # Background knowledge
    knowledge: dict[str, Any]
    # Hard control grant, immutable
    grant: Grant

    def to_json(self) -> str:
        """Dump to JSON for transport."""
        return json.dumps(asdict(self), indent=2)


class RoleCompiler:
    """Compile a role with precedence: task owner > acceptance > role instructions > knowledge."""

    def compile(
        self,
        role: LoadedRole,
        task: dict[str, Any],
        grants: dict[str, Any],
    ) -> CompiledRole:
        """Compile the role with task context and grants.

        Args:
            role: The loaded role with manifest and bundle.
            task: Task context: owner, acceptance, instructions, knowledge.
            grants: Hard control grants: limit, approval, capabilities, tools,
                paths, network, sandbox.

        Returns:
            A compiled role with precedence applied and grants as structured controls.
        """
        manifest = role.manifest

        # Extract task context with precedence
        task_owner_rules = task.get("owner", {})
        acceptance = task.get("acceptance", {})
        role_instructions = task.get("instructions", {})
        knowledge = task.get("knowledge", {})

        # Build hard control grant from grants dict or role manifest
        grant = self._build_grant(manifest, grants)

        return CompiledRole(
            role_id=manifest.id,
            role_version=manifest.version,
            role_summary=manifest.summary,
            task_owner_rules=task_owner_rules,
            acceptance=acceptance,
            role_instructions=role_instructions,
            knowledge=knowledge,
            grant=grant,
        )

    def _build_grant(self, manifest: RoleManifest, grants: dict[str, Any]) -> Grant:
        """Build a structured Grant from the role manifest and task grants.

        Task grants override role manifest defaults.
        """
        # Extract from manifest
        manifest_limit = manifest.limits
        manifest_approval = manifest.approval_thresholds

        # Extract from task grants, or fall back to manifest
        grant_limit = grants.get("limit", {})
        grant_approval = grants.get("approval", {})

        limit = GrantLimit(
            max_turns=grant_limit.get("max_turns", manifest_limit.max_turns),
            timeout_seconds=grant_limit.get("timeout_seconds", manifest_limit.timeout_seconds),
            max_tokens=grant_limit.get("max_tokens", manifest_limit.max_tokens),
        )

        approval = GrantApproval(
            max_files_changed=grant_approval.get(
                "max_files_changed", manifest_approval.max_files_changed
            ),
            max_lines_changed=grant_approval.get(
                "max_lines_changed", manifest_approval.max_lines_changed
            ),
        )

        # Capabilities and tools from task grants, with role manifest as fallback
        capabilities = tuple(grants.get("capabilities", manifest.capabilities))
        tools = tuple(grants.get("tools", manifest.tools))
        permitted_paths = tuple(grants.get("permitted_paths", manifest.permitted_paths))
        prohibited_writes = tuple(grants.get("prohibited_writes", manifest.prohibited_writes))
        network = grants.get("network", manifest.network)
        sandbox = grants.get("sandbox", manifest.sandbox)

        return Grant(
            limit=limit,
            approval=approval,
            capabilities=capabilities,
            tools=tools,
            permitted_paths=permitted_paths,
            prohibited_writes=prohibited_writes,
            network=network,
            sandbox=sandbox,
        )
