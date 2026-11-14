"""The ``queue`` broker adapter: keep the Standard's execution-queue history complete (#342).

The Agent Engineering Standard owns the queue, its hash-chained history and the repair CLI
(``work_queue.py history --check`` / ``history-repair``). This adapter copies nothing: it
fetches those modules once, at the commit pinned in ``ops/fleet.yaml``, through the forge
port, runs the CLI in a temporary directory with the fleet token in its environment only,
and maps exit codes and JSON to an owner-action status or a receipt.

Only the CLI's structured output is trusted. Free text from the CLI (which may carry
provider detail) never reaches a status, a receipt or the tracker; anything that is not a
recognised result raises, so the owner-actions tracker records a type-only "check failed".
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hyper_agents.application.ports import ForgePort
from hyper_agents.domain.owner_actions import Status

# work_queue.py exit codes: 0 ok, 2 malformed queue or history, 3 no handoff yet.
EXIT_OK, EXIT_FORMAT, EXIT_EMPTY = 0, 2, 3
# One maintenance check runs up to three CLI calls; keep the worst case well inside the job.
TIMEOUT_SECONDS = 90
PASS_THROUGH = ("PATH", "HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_GAP = re.compile(r"history is missing event (\d+) \(([0-9a-f]{12})\.\.\.\)")
_COMPLETE = re.compile(r"^history: ok, complete through seq (\d+)$")


class QueueCheckError(RuntimeError):
    """The CLI gave no recognised result (transport, auth, refusal): proves nothing."""


@dataclass(frozen=True)
class QueueConfig:
    repo: str
    ref: str
    issue: int
    tools: tuple[str, ...]


def load_queue_config(raw: Mapping[str, Any] | None) -> QueueConfig | None:
    if raw is None:
        return None
    tools = tuple(str(tool) for tool in cast(list[Any], raw["tools"]))
    if not any(tool.endswith("/work_queue.py") for tool in tools):
        raise ValueError("queue.tools must include the Standard's work_queue.py")
    ref = str(raw["ref"])
    if not _SHA.match(ref):
        # The tools run with the fleet token: an immutable commit, never a moving branch.
        raise ValueError("queue.ref must be a full 40-character commit SHA")
    return QueueConfig(repo=str(raw["repo"]), ref=ref, issue=int(raw["issue"]), tools=tools)


class QueueHistory:
    def __init__(
        self,
        forge: ForgePort,
        config: QueueConfig,
        token: Callable[[], str],
        actor: str = "hyper-agents-maintain",
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self._forge = forge
        self._config = config
        self._token = token
        self._actor = actor
        # Only what the CLI needs to reach the forge: PATH and proxy/CA settings, no secrets.
        self._base_env = dict(
            base_env
            if base_env is not None
            else {k: v for k, v in os.environ.items() if k in PASS_THROUGH}
        )
        self._sources: dict[str, str] | None = None

    def _tools(self) -> dict[str, str]:
        """Every configured tool, fetched once per instance; a missing one raises before
        anything runs."""
        if self._sources is None:
            self._sources = {
                Path(tool).name: self._forge.read_file(self._config.repo, self._config.ref, tool)
                for tool in self._config.tools
            }
        return self._sources

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        sources = self._tools()
        with tempfile.TemporaryDirectory(prefix="queue-tools-") as root:
            for name, text in sources.items():
                Path(root, name).write_text(text, encoding="utf-8")
            env = {
                **self._base_env,
                "GITHUB_TOKEN": self._token(),
                "AES_QUEUE_ACTOR": self._actor,
                "AES_QUEUE_HARNESS": "hyper-agents",
                "AES_QUEUE_PROVIDER": "none",
            }
            argv = [
                sys.executable,
                "-I",
                str(Path(root, "work_queue.py")),
                "--repo",
                self._config.repo,
                "--issue",
                str(self._config.issue),
                *args,
            ]
            return subprocess.run(  # noqa: S603 - argv list built here, no shell; pinned code
                argv,
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )

    @staticmethod
    def _report(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        try:
            parsed = json.loads(result.stdout)
        except ValueError:
            return {}
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}

    def status(self, _retried: bool = False) -> Status:
        """Done only when the chain is complete and the handoff reads back. A gap with an
        exact copy goes to the resolver; one without, or an unreadable handoff over a
        complete chain, needs the owner. Anything unrecognised raises (check failed)."""
        check = self._cli("history", "--check")
        line = check.stdout.strip()
        if check.returncode == EXIT_OK and (done := _COMPLETE.match(line)):
            handoff = self._cli("handoff", "show")
            if handoff.returncode in (EXIT_OK, EXIT_EMPTY):
                return Status(True, f"complete through seq {done.group(1)}; handoff reads back")
            if handoff.returncode == EXIT_FORMAT:
                return Status(
                    False,
                    f"history complete through seq {done.group(1)} but the handoff does not "
                    "match its digest; a history repair cannot fix this",
                    needs_owner=True,
                )
            raise QueueCheckError(f"handoff show exited {handoff.returncode}")
        gap = _GAP.search(line)
        if check.returncode != EXIT_FORMAT or not line.startswith("history: DEGRADED") or not gap:
            raise QueueCheckError(f"history --check exited {check.returncode}")
        detail = f"history is missing event {gap.group(1)} ({gap.group(2)}...)"
        probe = self._cli("history-repair")
        report = self._report(probe)
        found = report.get("status")
        if probe.returncode == EXIT_OK and found == "recoverable":
            seqs = ", ".join(f"{int(s)} ({str(f)[:12]})" for s, f in report.get("appended", []))
            return Status(False, f"{detail}; exact copy found for {seqs}, recovery dispatched")
        if probe.returncode == EXIT_OK and found == "complete" and not _retried:
            return self.status(_retried=True)  # closed concurrently between the two reads
        if probe.returncode == EXIT_FORMAT and found == "unrecoverable":
            return Status(
                False,
                f"{detail}; no exact copy in any comment, export or mirror: a versioned "
                "re-anchor needs the queue owner's authorization",
                needs_owner=True,
            )
        raise QueueCheckError(f"history-repair probe exited {probe.returncode}")

    def recover(self) -> dict[str, Any]:
        """Append the exact missing events. The Standard's CLI reads history and handoff
        back itself; its ``read_back`` (or an already complete chain) is the proof."""
        repair = self._cli("history-repair", "--apply")
        report = self._report(repair)
        status = report.get("status")
        if repair.returncode == EXIT_OK and status == "complete":
            return {
                "status": "complete",
                "seq": report.get("seq"),
                "appended": [],
                "verified": True,
            }
        if (
            repair.returncode == EXIT_OK
            and status == "recovered"
            and report.get("read_back") == {"history": "ok", "handoff": "ok"}
        ):
            return {
                "status": "recovered",
                "seq": report.get("seq"),
                "appended": report.get("appended", []),
                "verified": True,
            }
        if status == "unrecoverable":
            raise RuntimeError("queue history unrecoverable: no exact copy of the missing event")
        raise RuntimeError(f"queue history repair did not verify (exit {repair.returncode})")

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        if action == "check":
            status = self.status()
            return {"done": status.done, "detail": status.detail, "needs_owner": status.needs_owner}
        if action == "recover":
            return self.recover()
        raise ValueError(f"unknown queue action: {action}")


class UnconfiguredQueue:
    """Registered when ``ops/fleet.yaml`` declares no queue: a clear refusal, not a KeyError."""

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        raise ValueError("ops/fleet.yaml declares no queue")
