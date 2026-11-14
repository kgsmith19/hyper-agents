"""The ``queue`` broker adapter: keep the Standard's execution-queue history complete (#342).

The Agent Engineering Standard owns the queue, its hash-chained history and the repair CLI
(``work_queue.py history --check`` / ``history-repair``). This adapter copies nothing: it
fetches those modules at the configured ref through the forge port, runs the CLI in a
temporary directory with the fleet token in its environment, and maps the result to an
owner-action status or a receipt. Secret values never reach a status, a receipt or argv.
"""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hyper_agents.application.ports import ForgePort
from hyper_agents.domain.owner_actions import Status

EXIT_OK, EXIT_EMPTY = 0, 3  # work_queue.py: 3 = no handoff yet, which is not a fault
TIMEOUT_SECONDS = 300
PASS_THROUGH = ("PATH", "HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


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
    return QueueConfig(
        repo=str(raw["repo"]), ref=str(raw["ref"]), issue=int(raw["issue"]), tools=tools
    )


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

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Fetch every configured tool first (a missing one raises before anything runs),
        then run the Standard's CLI against the configured queue."""
        sources = {
            Path(tool).name: self._forge.read_file(self._config.repo, self._config.ref, tool)
            for tool in self._config.tools
        }
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
            return subprocess.run(  # noqa: S603 - argv list built here, no shell; code from the configured ref
                argv,
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )

    @staticmethod
    def _line(result: subprocess.CompletedProcess[str]) -> str:
        text = (result.stdout.strip() or result.stderr.strip()).splitlines()
        return text[-1][:300] if text else f"exit {result.returncode}"

    def status(self) -> Status:
        """Done only when the chain is complete and the handoff reads back. A gap with an
        exact copy available goes to the resolver; one without asks the owner at once."""
        check = self._cli("history", "--check")
        if check.returncode == EXIT_OK:
            handoff = self._cli("handoff", "show")
            if handoff.returncode not in (EXIT_OK, EXIT_EMPTY):
                return Status(
                    False, f"history complete but handoff unreadable: {self._line(handoff)}"
                )
            return Status(True, f"{self._line(check)}; handoff reads back")
        detail = self._line(check).split("; repair:")[0]
        probe = self._cli("history-repair")
        report = self._report(probe)
        if probe.returncode == EXIT_OK and report.get("status") == "recoverable":
            found = ", ".join(f"{seq} ({fp})" for seq, fp in report.get("appended", []))
            return Status(False, f"{detail}; exact copy found for {found}, recovery dispatched")
        return Status(
            False,
            f"{detail}; no exact copy in any comment, export or mirror: a versioned re-anchor "
            "needs the queue owner's authorization",
            needs_owner=True,
        )

    @staticmethod
    def _report(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        try:
            return cast(dict[str, Any], json.loads(result.stdout))
        except ValueError:
            return {}

    def recover(self) -> dict[str, Any]:
        """Append the exact missing events, then prove the chain and handoff read back."""
        repair = self._cli("history-repair", "--apply")
        report = self._report(repair)
        if repair.returncode != EXIT_OK:
            raise RuntimeError(
                f"queue history unrecoverable: {report.get('status', self._line(repair))}"
            )
        after = self.status()
        if not after.done:
            raise RuntimeError(f"queue history repair did not verify: {after.detail}")
        return {"repair": report, "verified": True, "detail": after.detail}

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        if action == "check":
            status = self.status()
            return {"done": status.done, "detail": status.detail}
        if action == "recover":
            return self.recover()
        raise ValueError(f"unknown queue action: {action}")
