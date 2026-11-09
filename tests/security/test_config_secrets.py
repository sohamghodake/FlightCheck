"""A flightcheck.toml holding an API key value is refused with exit 2 and the value is never
printed (plan §20.2, ADR-023 item 1), through every command that reads the file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from flightcheck import orchestrator
from flightcheck.cli.app import app
from flightcheck.mock.server import MockServer
from flightcheck.reporting.json_report import REPORT_FILENAME
from flightcheck.runtime import harness

TOML_VALUE = "fcleak" + "tomlvalue000009"  # matches no redaction pattern on purpose
runner = CliRunner()

PLACEMENTS = {
    "provider api_key": f'[providers.bharatcode]\napi_key = "{TOML_VALUE}"\n',
    "provider token": f'[providers.bharatcode]\ntoken = "{TOML_VALUE}"\n',
    "explain secret": f'[explain]\nclient_secret = "{TOML_VALUE}"\n',
    "nested password": f'[providers.mine]\nkind = "bharatcode"\nbase_url = "https://x"\n'
    f'password = "{TOML_VALUE}"\n',
}


@pytest.fixture
def no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not start a mock server or a process")

    monkeypatch.setattr(MockServer, "start", boom)
    monkeypatch.setattr(harness.subprocess, "Popen", boom)
    monkeypatch.setattr(orchestrator, "run_app", boom)


def _project(tmp_path: Path, toml: str) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "flightcheck.toml").write_text(toml + '\n[test]\ncommand = ["python", "main.py"]\n')
    return root


def _clean_report(tmp_path: Path) -> Path:
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "main.py").write_text("print('hi')\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(clean), "--output", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    return tmp_path / "out" / REPORT_FILENAME


@pytest.mark.parametrize("placement", sorted(PLACEMENTS))
def test_every_command_refuses_a_key_in_flightcheck_toml(
    tmp_path: Path, no_execution: None, placement: str
) -> None:
    root = _project(tmp_path, PLACEMENTS[placement])
    report = _clean_report(tmp_path)
    for args in (
        ["scan", str(root), "--output", str(tmp_path / "o1")],
        ["test", str(root), "--allow-exec", "--output", str(tmp_path / "o2")],
        ["explain", str(report), "--repo", str(root), "--llm", "fake"],
    ):
        result = runner.invoke(app, [*args, "-vv", "--no-color"])
        assert result.exit_code == 2, (placement, args, result.output)
        assert "must not contain secrets" in result.stderr, (placement, args)
        assert TOML_VALUE not in result.output, (placement, args)
    for written in tmp_path.rglob("*"):
        if written.is_file() and written.name != "flightcheck.toml":
            assert TOML_VALUE not in written.read_text(encoding="utf-8", errors="replace")


def test_api_key_env_names_are_allowed(tmp_path: Path) -> None:
    root = _project(tmp_path, '[providers.bharatcode]\napi_key_env = "MY_KEY_VAR"\n')
    result = runner.invoke(app, ["scan", str(root), "--output", str(tmp_path / "o")])
    assert result.exit_code == 0, result.output
