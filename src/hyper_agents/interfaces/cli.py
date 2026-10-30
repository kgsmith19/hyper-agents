"""The `hyper-agents` command line."""

import json
import platform
import shutil
import sys
from importlib import metadata

import typer

from hyper_agents import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def version() -> None:
    """Print the hyper-agents version."""
    typer.echo(__version__)


def _probe(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def doctor_report() -> dict[str, object]:
    """What this machine can run today, as verified facts, never assumptions."""
    sdk = _probe("openhands-sdk")
    return {
        "hyper_agents": __version__,
        "python": platform.python_version(),
        "python_ok": sys.version_info >= (3, 12),
        "docker_cli": shutil.which("docker") is not None,
        # The executor adapter arrives in P03; until then no real worker can run.
        "openhands_sdk": sdk or "not installed (executor arrives in P03)",
        "real_workers_available": False,
    }


@app.command()
def doctor(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")) -> None:
    """Report runtime prerequisites. Exits 1 when a hard requirement is missing."""
    report = doctor_report()
    if as_json:
        typer.echo(json.dumps(report, sort_keys=True))
    else:
        for key, value in report.items():
            typer.echo(f"{key}: {value}")
    if not report["python_ok"]:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
