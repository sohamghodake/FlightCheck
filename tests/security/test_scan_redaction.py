"""Planted secrets never reach the JSON report, the terminal output or any finding (§26.2).

Each form is planted where several rules fire on the same line (FC-GEN-005 on the client,
FC-GEN-004 on the call, FC-SEC-001/002), so every rule's excerpt and message is covered.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from flightcheck.cli.app import app
from flightcheck.core.config import FlightCheckConfig
from flightcheck.core.models import ScanRequest
from flightcheck.core.redact import _registered
from flightcheck.orchestrator import run_scan
from flightcheck.reporting.json_report import REPORT_FILENAME

REPO_ROOT = Path(__file__).resolve().parents[2]
# Built by concatenation so this test file itself holds no complete key literal.
BC_KEY = "bc_live_" + "PLANTED1111111111111111"
SK_KEY = "sk-" + "PLANTED22222222222222222"
SHORT_KEY = "zq9x"
DICT_KEY = "dk" + "77665544"
TINY_KEY = "q7"
SECRETS = [BC_KEY, SK_KEY, SHORT_KEY, DICT_KEY, TINY_KEY]

APP = f'''import logging

from openai import OpenAI

logger = logging.getLogger(__name__)
API_KEY = "{BC_KEY}"
a = OpenAI(base_url="https://bharatcode.ai/api/model/v1", api_key="{SHORT_KEY}")
b = OpenAI(base_url="https://bharatcode.ai/api/model/v1", api_key="{TINY_KEY}")
HEADERS = {{"Authorization": "Bearer {SK_KEY}"}}
SETTINGS = {{"api_key": "{DICT_KEY}", "model": "gpt-4o"}}


def ask(api_key):
    print("key", api_key, "{BC_KEY}")
    logger.info("using %s", api_key)
    return a.responses.create(model="gpt-4o", input="hi", store=True, metadata={{"k": "{SK_KEY}"}})
'''


@pytest.fixture
def planted(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "app"
    root.mkdir()
    (root / "main.py").write_text(APP, encoding="utf-8")
    yield root


def _assert_absent(text: str) -> None:
    for secret in SECRETS:
        assert f'"{secret}"' not in text and f"'{secret}'" not in text, secret
        if len(secret) >= 8:
            assert secret not in text, secret


def test_run_scan_findings_never_contain_planted_secrets(planted: Path) -> None:
    report = run_scan(ScanRequest(root=planted), FlightCheckConfig())
    rules = {f.rule_id for f in report.findings}
    assert {"FC-SEC-001", "FC-SEC-002", "FC-GEN-004", "FC-GEN-005", "FC-BC-001"} <= rules
    for finding in report.findings:
        _assert_absent(finding.model_dump_json())
    _assert_absent(report.model_dump_json())
    # every planted literal is reported by FC-SEC-001
    sec = [f for f in report.findings if f.rule_id == "FC-SEC-001"]
    assert {f.location.line for f in sec} == {6, 7, 8, 9, 10, 14, 16}


def test_cli_json_and_terminal_never_contain_planted_secrets(planted: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        app, ["scan", str(planted), "--output", str(out_dir), "--fail-on", "none", "-v"]
    )
    assert result.exit_code == 0, result.output
    _assert_absent(result.output)
    report_text = (out_dir / REPORT_FILENAME).read_text(encoding="utf-8")
    _assert_absent(report_text)
    assert "bc_live_…[REDACTED]" in report_text
    assert json.loads(report_text)["findings"]


def test_scan_leaves_no_global_redaction_state(planted: Path) -> None:
    before = set(_registered)
    run_scan(ScanRequest(root=planted), FlightCheckConfig())
    assert set(_registered) == before


def test_bc_bad_secrets_app_shows_only_the_redacted_key(tmp_path: Path) -> None:
    target = REPO_ROOT / "tests" / "fixtures" / "apps" / "bc_bad_secrets"
    raw = "bc_live_" + "FAKE0000000000000000"
    result = CliRunner().invoke(app, ["scan", str(target), "--output", str(tmp_path)])
    assert result.exit_code == 1  # FC-SEC-001 is critical
    assert raw not in result.output
    assert "bc_live_…[REDACTED]" in result.output
    assert raw not in (tmp_path / REPORT_FILENAME).read_text(encoding="utf-8")
