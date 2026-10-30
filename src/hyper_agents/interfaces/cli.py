"""The `hyper-agents` command line."""

import json
import platform
import shutil
import sys
from importlib import metadata
from pathlib import Path

import typer

from hyper_agents import __version__
from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.catalog import CatalogLoader, CatalogValidator
from hyper_agents.domain.errors import InvalidInput, PolicyDenied

app = typer.Typer(no_args_is_help=True, add_completion=False)
roles_app = typer.Typer(no_args_is_help=True, help="Manage roles: list, show, validate, compile.")
app.add_typer(roles_app, name="roles")


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
        "openhands_sdk": sdk or "not installed (P03 requires openhands-sdk)",
        "real_workers_available": sdk is not None,
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


def _catalog_root() -> Path:
    """Find the agents/catalog directory."""
    root = Path(__file__).parent.parent.parent.parent / "agents" / "catalog"
    if not root.is_dir():
        raise InvalidInput(f"catalog not found at {root}")
    return root


@roles_app.command()
def list() -> None:
    """List all available roles."""
    try:
        catalog_root = _catalog_root()
        loader = CatalogLoader(catalog_root, SafeYamlParser())
        registry = loader.load()
        for role_id in registry.list():
            typer.echo(role_id)
    except (InvalidInput, PolicyDenied) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None


@roles_app.command()
def show(role: str) -> None:
    """Show a role's manifest."""
    try:
        catalog_root = _catalog_root()
        loader = CatalogLoader(catalog_root, SafeYamlParser())
        registry = loader.load()
        loaded = registry.get(role)
        typer.echo(json.dumps(loaded.manifest.model_dump(), indent=2))
    except InvalidInput as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None
    except (PolicyDenied, Exception) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None


@roles_app.command()
def validate() -> None:
    """Validate all roles in the catalog."""
    try:
        catalog_root = _catalog_root()
        loader = CatalogLoader(catalog_root, SafeYamlParser())
        registry = loader.load()
        validator = CatalogValidator()
        validator.validate(registry)
        typer.echo(f"✓ {len(registry.list())} role(s) valid")
    except (InvalidInput, PolicyDenied) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
