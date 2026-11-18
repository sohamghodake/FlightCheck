"""Rule gating for Groq call sites (ADR-036; G3 mode table in ADR-037).

The G3 rule-level cells (Groq rules on/off per mode) are in tests/scanner/test_groq_rule_gating.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from flightcheck.cli.app import app
from flightcheck.core.config import FlightCheckConfig, load_config
from flightcheck.core.errors import ConfigError
from flightcheck.core.models import ScanRequest
from flightcheck.orchestrator import _provider_mode, run_scan
from flightcheck.reporting.json_report import REPORT_FILENAME, read_report

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "tests" / "fixtures" / "rules"

# Requests that break BharatCode's contract (store=True, budget > 32 000, unknown model) and
# have no error handling, so FC-GEN-004 fires; sent through Groq-classified clients.
GROQ_APP = (
    "import os\n"
    "from groq import Groq\n"
    "from openai import OpenAI\n"
    "groq_client = Groq()\n"
    "compat = OpenAI(base_url='https://api.groq.com/openai/v1')\n"
    "def ask():\n"
    "    groq_client.chat.completions.create(model='m', messages=[], max_tokens=40000)\n"
    "    return compat.responses.create(model='m', input='x', store=True, background=True)\n"
)
BC_APP = (
    "from openai import OpenAI\n"
    "bc = OpenAI(base_url='https://bharatcode.ai/api/model/v1')\n"
    "def ask():\n"
    "    return bc.responses.create(model='m', input='x', store=True)\n"
)


def _ids(report) -> set[str]:
    return {f.rule_id for f in report.findings}


def _by_file(report) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in report.findings:
        if f.location is not None:
            out.setdefault(f.location.path, set()).add(f.rule_id)
    return out


def test_groq_call_sites_get_only_provider_neutral_rules(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(GROQ_APP, encoding="utf-8")
    report = run_scan(ScanRequest(root=tmp_path), FlightCheckConfig())
    providers = {d.provider for d in report.providers_detected}
    assert providers == {"groq"}
    assert not [r for r in _ids(report) if r.startswith("FC-BC-")]
    assert "FC-GEN-004" in _ids(report)  # provider-neutral rules still run
    assert {f.provider for f in report.findings if f.provider} == {"groq"}


def test_mixed_repo_runs_bharatcode_rules_only_on_bharatcode_call_sites(tmp_path: Path) -> None:
    (tmp_path / "groq_app.py").write_text(GROQ_APP, encoding="utf-8")
    (tmp_path / "bc_app.py").write_text(BC_APP, encoding="utf-8")
    report = run_scan(ScanRequest(root=tmp_path), FlightCheckConfig())
    by_file = _by_file(report)
    assert "FC-BC-001" in by_file["bc_app.py"]
    assert not [r for r in by_file["groq_app.py"] if r.startswith("FC-BC-")]


def test_provider_groq_override_disables_bharatcode_rules_everywhere(tmp_path: Path) -> None:
    (tmp_path / "bc_app.py").write_text(BC_APP, encoding="utf-8")
    report = run_scan(ScanRequest(root=tmp_path, provider_override="groq"), FlightCheckConfig())
    assert not [r for r in _ids(report) if r.startswith("FC-BC-")]
    assert "FC-GEN-004" in _ids(report)
    assert report.metadata.options["provider"] == "groq"


def test_project_provider_groq_declares_groq_and_keeps_auto_mode(tmp_path: Path) -> None:
    (tmp_path / "flightcheck.toml").write_text('[project]\nprovider = "groq"\n', encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "from openai import OpenAI\nc = OpenAI()\nc.responses.create(model='m', store=True)\n",
        encoding="utf-8",
    )
    config = load_config(tmp_path, environ={})
    assert _provider_mode(ScanRequest(root=tmp_path), config) == ("auto", "auto", "groq")
    report = run_scan(ScanRequest(root=tmp_path), config)
    call = next(d for d in report.providers_detected if d.location.path == "app.py")
    assert (call.provider, call.confidence.value) == ("groq", "high")
    assert "flightcheck.toml provider = groq" in call.signals
    assert not [r for r in _ids(report) if r.startswith("FC-BC-")]


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("groq", ("disabled", "forced", "groq")),
        ("bharatcode", ("forced", "disabled", "bharatcode")),
        ("openai_compatible", ("disabled", "disabled", None)),
        (None, ("auto", "auto", None)),
    ],
)
def test_provider_mode_table(override: str | None, expected: tuple) -> None:
    request = ScanRequest(root=Path("."), provider_override=override)
    assert _provider_mode(request, FlightCheckConfig()) == expected


def test_unknown_provider_hint_names_groq() -> None:
    with pytest.raises(ConfigError) as info:
        _provider_mode(ScanRequest(root=Path("."), provider_override="nope"), FlightCheckConfig())
    assert "groq" in (info.value.hint or "")


def test_cli_scan_provider_groq(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["scan", str(RULES / "FC-BC-001"), "--provider", "groq", "--output", str(tmp_path)],
    )
    report = read_report(tmp_path / REPORT_FILENAME)
    assert result.exit_code == 1, result.output  # FC-SEC-001 (high) still counts
    assert not [r for r in _ids(report) if r.startswith("FC-BC-")]
    assert {"FC-GEN-004", "FC-GEN-005"} <= _ids(report)
    assert report.metadata.options["provider"] == "groq"


def test_cli_help_lists_groq() -> None:
    result = CliRunner().invoke(app, ["scan", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "groq" in result.output
