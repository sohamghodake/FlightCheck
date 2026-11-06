import os

from typer.testing import CliRunner

from flightcheck import __version__
from flightcheck.cli.app import app

runner = CliRunner()


def test_version_flag_prints_version_and_exits_zero() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "flightcheck 0.1.0"


def test_version_matches_package_metadata() -> None:
    assert __version__ == "0.1.0"


def test_no_network_env_is_set() -> None:
    assert os.environ.get("FLIGHTCHECK_NO_NETWORK") == "1"
