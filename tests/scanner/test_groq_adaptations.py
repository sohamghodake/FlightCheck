"""Provider-neutral rules on Groq call sites (contract §4.3, phase G3, ADR-037).

IDs and meanings are unchanged (ADR-012); only text, remediation and documentation links cite
Groq on call sites classified ``groq``. Non-Groq call sites keep their previous wording.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from typer.testing import CliRunner

from flightcheck.cli.app import app
from flightcheck.core.config import FlightCheckConfig
from flightcheck.core.models import Finding, Report, ScanRequest
from flightcheck.diagnostics import service as service_module
from flightcheck.orchestrator import run_scan
from flightcheck.reporting.json_report import REPORT_FILENAME, read_report

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "tests" / "fixtures" / "rules"
GROQ_KEY = "gsk_" + "FAKEtestKey0000000000000"
BC_KEY = "bc_live_" + "FAKEtestKey00000000"


def _scan(tmp_path: Path, source: str, name: str = "app.py") -> Report:
    (tmp_path / name).write_text(source, encoding="utf-8")
    return run_scan(ScanRequest(root=tmp_path), FlightCheckConfig())


def _one(report: Report, rule_id: str) -> Finding:
    found = [f for f in report.findings if f.rule_id == rule_id]
    assert len(found) == 1, [f.rule_id for f in report.findings]
    return found[0]


# --- FC-GEN-005 ------------------------------------------------------------------------------


def test_groq_client_without_timeout_is_info(tmp_path: Path) -> None:
    finding = _one(_scan(tmp_path, "from groq import Groq\nc = Groq()\n"), "FC-GEN-005")
    assert (finding.severity.value, finding.confidence.value) == ("info", "high")
    assert "60 s" in finding.message and "contract K5" in finding.message
    assert "10 minutes" not in finding.message
    assert "Groq(" in finding.remediation and finding.provider == "groq"
    assert finding.doc_url and finding.doc_url.startswith("https://console.groq.com/docs/")


def test_groq_client_with_timeout_or_with_options_is_fine(tmp_path: Path) -> None:
    assert not _scan(tmp_path, "from groq import Groq\nc = Groq(timeout=30.0)\n").findings
    source = "from groq import Groq\nc = Groq().with_options(timeout=30.0)\n"
    assert not [f for f in _scan(tmp_path, source).findings if f.rule_id == "FC-GEN-005"]


def test_openai_client_on_groq_keeps_low_and_cites_the_openai_default(tmp_path: Path) -> None:
    source = "from openai import OpenAI\nc = OpenAI(base_url='https://api.groq.com/openai/v1')\n"
    finding = _one(_scan(tmp_path, source), "FC-GEN-005")
    assert finding.severity.value == "low"
    assert "OpenAI SDK default is 10 minutes" in finding.message
    assert "contract ST5" in finding.message and "BharatCode" not in finding.message


def test_bharatcode_client_message_is_unchanged(tmp_path: Path) -> None:
    source = (
        "from openai import OpenAI\nc = OpenAI(base_url='https://bharatcode.ai/api/model/v1')\n"
    )
    finding = _one(_scan(tmp_path, source), "FC-GEN-005")
    assert finding.message == (
        "`openai.OpenAI` client created without `timeout=` and no `.with_options(timeout=...)` "
        "in this file: the OpenAI SDK default is 10 minutes [VERIFIED] and BharatCode requests "
        "may queue [VERIFIED], so set an explicit, deliberate timeout."
    )
    assert finding.doc_url == "https://bharatcode.ai/build"


def test_httpx_client_no_longer_claims_the_openai_default(tmp_path: Path) -> None:
    finding = _one(_scan(tmp_path, "import httpx\nc = httpx.Client()\n"), "FC-GEN-005")
    assert "10 minutes" not in finding.message and "`httpx` applies" in finding.message


# --- FC-GEN-001…004 --------------------------------------------------------------------------

GROQ_HEAD = "from groq import Groq\nclient = Groq(timeout=60.0)\n"
BC_HEAD = (
    "from openai import OpenAI\n"
    "client = OpenAI(base_url='https://bharatcode.ai/api/model/v1', timeout=300.0)\n"
)


def test_gen_004_on_groq_cites_429_and_x_groq_id(tmp_path: Path) -> None:
    source = f"{GROQ_HEAD}def ask(m):\n    return client.chat.completions.create(messages=m)\n"
    finding = _one(_scan(tmp_path, source), "FC-GEN-004")
    assert "429" in finding.message and "503 (model busy)" not in finding.message
    assert "retry-after" in finding.remediation and "x_groq.id" in finding.remediation
    assert finding.doc_url == "https://console.groq.com/docs/errors"
    assert (finding.severity.value, finding.confidence.value) == ("medium", "medium")


def test_gen_004_on_bharatcode_is_unchanged(tmp_path: Path) -> None:
    source = f"{BC_HEAD}def ask(m):\n    return client.chat.completions.create(messages=m)\n"
    finding = _one(_scan(tmp_path, source), "FC-GEN-004")
    assert "a 503 (model busy), timeout or 4xx error may crash the app." in finding.message
    assert "on 503 back off" in finding.remediation
    assert finding.doc_url == "https://bharatcode.ai/build"


def test_gen_001_and_002_remediation_on_groq(tmp_path: Path) -> None:
    source = (
        f"{GROQ_HEAD}import openai\n"
        "compat = openai.OpenAI(base_url='https://api.groq.com/openai/v1', timeout=60.0)\n"
        "def ask(m):\n"
        "    try:\n"
        "        r = client.chat.completions.create(messages=m)\n"
        "        o = compat.responses.create(input=m)\n"
        "    except Exception:\n"
        "        return None\n"
        "    return r.choices[0].message.content, o.output_text\n"
    )
    report = _scan(tmp_path, source)
    gen1, gen2 = _one(report, "FC-GEN-001"), _one(report, "FC-GEN-002")
    assert "max_completion_tokens" in gen1.remediation and "32 000" not in gen1.remediation
    assert "contract C11" in gen1.message
    assert "reasoning tokens" in gen2.remediation and "32 000" not in gen2.remediation
    assert "contract R10" in gen2.message


def test_gen_003_on_groq_names_x_groq_error_and_needs_finish_reason(tmp_path: Path) -> None:
    # Checking only x_groq.error does not reveal truncation: still flagged (ADR-037).
    source = (
        f"{GROQ_HEAD}def ask(m):\n"
        "    try:\n"
        "        for chunk in client.chat.completions.create(messages=m, stream=True):\n"
        "            if chunk.x_groq and chunk.x_groq.error:\n"
        "                raise RuntimeError(chunk.x_groq.error)\n"
        "            print(chunk.choices[0].delta.content)\n"
        "    except RuntimeError:\n"
        "        return None\n"
    )
    finding = _one(_scan(tmp_path, source), "FC-GEN-003")
    assert "x_groq.error" in finding.message and "x_groq.error" in finding.remediation
    assert "observed in" in finding.message and finding.confidence.value == "medium"


# --- FC-GEN-006/007 (file-level provider) ----------------------------------------------------

TOOL_LOOP = (
    "import json\n"
    "def run(m):\n"
    "    r = client.chat.completions.create(messages=m)\n"
    "    call = r.choices[0].message.tool_calls[0]\n"
    "    args = json.loads(call.function.arguments)\n"
    "    return {'role': 'tool', 'tool_call_id': call.id, 'content': {'ok': args}}\n"
)


def test_gen_006_and_007_cite_groq_in_groq_files(tmp_path: Path) -> None:
    report = _scan(tmp_path, GROQ_HEAD + TOOL_LOOP)
    gen6, gen7 = _one(report, "FC-GEN-006"), _one(report, "FC-GEN-007")
    assert "failed_generation" in gen6.remediation
    assert "contract T5" in gen7.message and "BharatCode" not in gen7.message
    assert (
        gen7.doc_url == gen6.doc_url == "https://console.groq.com/docs/tool-use/local-tool-calling"
    )


def test_gen_006_and_007_keep_bharatcode_wording_elsewhere(tmp_path: Path) -> None:
    report = _scan(tmp_path, BC_HEAD + TOOL_LOOP)
    gen6, gen7 = _one(report, "FC-GEN-006"), _one(report, "FC-GEN-007")
    assert "failed_generation" not in gen6.remediation and gen6.doc_url is None
    assert "BharatCode requires a string tool output" in gen7.message


# --- FC-SEC-001 ------------------------------------------------------------------------------


def test_groq_key_is_critical_masked_and_names_groq_api_key(tmp_path: Path) -> None:
    out = tmp_path / "out"
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.py").write_text(
        f"from groq import Groq\nclient = Groq(api_key='{GROQ_KEY}', timeout=60.0)\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app, ["scan", str(app_dir), "--output", str(out), "--format", "all"]
    )
    assert result.exit_code == 1, result.output
    assert GROQ_KEY not in result.output
    for path in out.iterdir():
        assert GROQ_KEY not in path.read_text(encoding="utf-8"), path
    finding = _one(read_report(out / REPORT_FILENAME), "FC-SEC-001")
    assert (finding.severity.value, finding.confidence.value) == ("critical", "high")
    assert "gsk_…[REDACTED]" in finding.message
    assert "GROQ_API_KEY" in finding.remediation
    assert all(GROQ_KEY not in (e.excerpt or "") for e in finding.evidence)


def test_short_gsk_values_are_not_keys(tmp_path: Path) -> None:
    assert not _scan(tmp_path, "PREFIX = 'gsk_'\nX = 'gsk_abc'\n").findings


@pytest.mark.parametrize(
    ("source", "severity", "env_name"),
    [
        ("GROQ_API_KEY = 'abc12345'\n", "high", "GROQ_API_KEY"),
        (
            "from groq import Groq\nc = Groq(api_key='abc12345', timeout=1.0)\n",
            "high",
            "GROQ_API_KEY",
        ),
        (
            "from groq import Groq\nc = Groq(api_key='gsk_...', timeout=1.0)\n",
            "info",
            "GROQ_API_KEY",
        ),
        (f"KEY = '{BC_KEY}'\n", "critical", "BHARATCODE_API_KEY"),
        ("OPENAI_API_KEY = 'abc12345'\n", "high", None),
    ],
)
def test_sec_001_remediation_follows_the_key_provider(
    tmp_path: Path, source: str, severity: str, env_name: str | None
) -> None:
    finding = _one(_scan(tmp_path, source), "FC-SEC-001")
    assert finding.severity.value == severity
    if env_name is None:
        assert "GROQ_API_KEY" in finding.remediation  # the neutral example
        assert "provider's API-key" in finding.remediation
        assert "BHARATCODE" not in finding.remediation
    else:
        assert env_name in finding.remediation


def test_groq_placeholder_literal_is_info(tmp_path: Path) -> None:
    placeholder = "gsk_" + "your_secret_key_here"
    finding = _one(_scan(tmp_path, f"EXAMPLE = '{placeholder}'\n"), "FC-SEC-001")
    assert finding.severity.value == "info" and "GROQ_API_KEY" in finding.message


def test_flightcheck_source_has_no_key_findings() -> None:
    """Trap 3: src holds Groq hosts, model IDs and the gsk_ pattern; FC-SEC-001 stays silent."""
    report = run_scan(ScanRequest(root=REPO_ROOT / "src"), FlightCheckConfig())
    assert not [f for f in report.findings if f.rule_id.startswith(("FC-SEC-", "FC-GQ-"))]


# --- explain with Groq findings --------------------------------------------------------------


def test_explain_fake_handles_groq_findings(tmp_path: Path, monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(service_module, "build_adapter", refuse)
    monkeypatch.setattr(httpx2.Client, "send", refuse)
    runner = CliRunner()
    out = tmp_path / "out"
    target = RULES / "FC-GQ-007"
    scan = runner.invoke(app, ["scan", str(target), "--output", str(out), "--format", "json"])
    assert scan.exit_code == 1, scan.output
    report_path = out / REPORT_FILENAME
    assert any(f.rule_id == "FC-GQ-007" for f in read_report(report_path).findings)
    result = runner.invoke(
        app, ["explain", str(report_path), "--llm", "fake", "--yes", "--repo", str(target)]
    )
    assert result.exit_code == 0, result.output
    assert "FC-GQ-007" in result.stdout
    explained = json.loads(
        report_path.with_name("flightcheck-report.explained.json").read_text(encoding="utf-8")
    )
    assert explained["ai_suggestions"]
    assert {f["rule_id"] for f in explained["findings"]} == {
        f.rule_id for f in read_report(report_path).findings
    }
