import json

from typer.testing import CliRunner

from hyper_agents import __version__
from hyper_agents.interfaces.cli import app

runner = CliRunner()


def test_version_prints_the_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_doctor_reports_facts_and_never_claims_real_workers_before_p03() -> None:
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["python_ok"] is True
    assert report["real_workers_available"] is False
