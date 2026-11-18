"""Groq rule gating, every cell of the ADR-037 mode table (phase G3).

Modes per rule family: ``auto`` (call sites classified as the family's provider), ``forced``
(every call site, no confidence downgrade), ``disabled`` (never). FC-GEN/FC-SEC rules are not
gated (ADR-026).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flightcheck.core.config import FlightCheckConfig, ProviderConfig, load_config
from flightcheck.core.models import Report, ScanRequest
from flightcheck.orchestrator import _provider_mode, run_scan

# n=2 breaks Groq's contract (FC-GQ-008, C4); store=True breaks BharatCode's (FC-BC-001).
GROQ_APP = (
    "import groq\n"
    "from groq import Groq\n"
    "client = Groq(timeout=60.0)\n"
    "def ask(messages):\n"
    "    try:\n"
    "        return client.chat.completions.create(model='openai/gpt-oss-20b', "
    "messages=messages, n=2)\n"
    "    except groq.APIError:\n"
    "        return None\n"
)
BC_APP = (
    "import openai\n"
    "from openai import OpenAI\n"
    "bc = OpenAI(base_url='https://bharatcode.ai/api/model/v1', timeout=300.0)\n"
    "def ask(messages):\n"
    "    try:\n"
    "        bc.chat.completions.create(model='qwen-3.8-27b', messages=messages, n=2)\n"
    "        return bc.responses.create(model='qwen-3.8-27b', input=messages, store=True)\n"
    "    except openai.APIError:\n"
    "        return None\n"
)
# An OpenAI-compatible server that is neither Groq nor BharatCode, sending a Groq model ID:
# the model alone never makes a call site Groq (trap 2, ADR-036 item 1).
COMPAT_APP = (
    "import openai\n"
    "from openai import OpenAI\n"
    "local = OpenAI(base_url='http://localhost:8000/v1', api_key='EMPTY', timeout=30.0)\n"
    "def ask(messages):\n"
    "    try:\n"
    "        return local.chat.completions.create(model='openai/gpt-oss-20b', "
    "messages=messages, n=2)\n"
    "    except openai.APIError:\n"
    "        return None\n"
)
# The groq SDK pointed at another host with /openai/v1: a high-confidence conflict (unknown)
# for detection, but FC-GQ-015's double prefix applies to any host.
CONFLICT_CLIENT = "from groq import Groq\nc = Groq(base_url='https://bharatcode.ai/openai/v1')\n"


def _repo(tmp_path: Path, provider: str | None = None) -> Path:
    (tmp_path / "groq_app.py").write_text(GROQ_APP, encoding="utf-8")
    (tmp_path / "bc_app.py").write_text(BC_APP, encoding="utf-8")
    (tmp_path / "compat_app.py").write_text(COMPAT_APP, encoding="utf-8")
    if provider is not None:
        (tmp_path / "flightcheck.toml").write_text(
            f'[project]\nprovider = "{provider}"\n', encoding="utf-8"
        )
    return tmp_path


def _by_file(report: Report, prefix: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in report.findings:
        if f.location is not None and f.rule_id.startswith(prefix):
            out.setdefault(f.location.path, set()).add(f.rule_id)
    return out


def _scan(root: Path, override: str | None = None) -> Report:
    config = load_config(root, environ={})
    return run_scan(ScanRequest(root=root, provider_override=override), config)


# --- _provider_mode: every row of the ADR-037 table ----------------------------------------


@pytest.mark.parametrize(
    ("override", "project", "expected"),
    [
        (None, None, ("auto", "auto", None)),
        ("bharatcode", None, ("forced", "disabled", "bharatcode")),
        ("groq", None, ("disabled", "forced", "groq")),
        ("openai_compatible", None, ("disabled", "disabled", None)),
        (None, "bharatcode", ("auto", "auto", "bharatcode")),
        (None, "groq", ("auto", "auto", "groq")),
        (None, "openai_compatible", ("disabled", "disabled", None)),
        ("bharatcode", "groq", ("forced", "disabled", "bharatcode")),  # the flag wins
        ("groq", "bharatcode", ("disabled", "forced", "groq")),
    ],
)
def test_provider_mode_table(
    override: str | None, project: str | None, expected: tuple[str, str, str | None]
) -> None:
    config = FlightCheckConfig(provider=project)
    request = ScanRequest(root=Path("."), provider_override=override)
    assert _provider_mode(request, config) == expected


def test_custom_profile_kind_decides_the_mode() -> None:
    providers = dict(FlightCheckConfig().providers)
    providers["mygroq"] = ProviderConfig(
        name="mygroq", kind="groq", base_url="https://api.groq.com/openai/v1"
    )
    providers["local"] = ProviderConfig(
        name="local", kind="openai_compatible", base_url="http://localhost:8000/v1"
    )
    request = ScanRequest(root=Path("."))
    assert _provider_mode(request, FlightCheckConfig(provider="mygroq", providers=providers)) == (
        "auto",
        "auto",
        "groq",
    )
    assert _provider_mode(request, FlightCheckConfig(provider="local", providers=providers)) == (
        "disabled",
        "disabled",
        None,
    )


# --- rule-level cells ------------------------------------------------------------------------


def test_auto_runs_each_family_only_on_its_own_call_sites(tmp_path: Path) -> None:
    report = _scan(_repo(tmp_path))
    assert _by_file(report, "FC-GQ-") == {"groq_app.py": {"FC-GQ-008"}}
    assert _by_file(report, "FC-BC-") == {"bc_app.py": {"FC-BC-001"}}


def test_provider_groq_forces_groq_rules_everywhere_and_disables_bharatcode(
    tmp_path: Path,
) -> None:
    report = _scan(_repo(tmp_path), "groq")
    gq = _by_file(report, "FC-GQ-")
    assert gq["groq_app.py"] == {"FC-GQ-008"}
    assert gq["compat_app.py"] == {"FC-GQ-008"}
    assert "FC-GQ-008" in gq["bc_app.py"]
    assert _by_file(report, "FC-BC-") == {}


def test_provider_bharatcode_disables_groq_rules(tmp_path: Path) -> None:
    report = _scan(_repo(tmp_path), "bharatcode")
    assert _by_file(report, "FC-GQ-") == {}
    assert "FC-BC-001" in _by_file(report, "FC-BC-")["bc_app.py"]


def test_provider_openai_compatible_disables_both_families(tmp_path: Path) -> None:
    report = _scan(_repo(tmp_path), "openai_compatible")
    assert _by_file(report, "FC-GQ-") == {} and _by_file(report, "FC-BC-") == {}
    assert _by_file(report, "FC-SEC-") == {"compat_app.py": {"FC-SEC-001"}}  # still run


def test_project_provider_groq_keeps_auto_mode(tmp_path: Path) -> None:
    # Declared groq joins every client's signals: the compatible client becomes groq, the
    # BharatCode client a conflict (unknown); auto mode then gates by that classification.
    report = _scan(_repo(tmp_path, "groq"))
    gq = _by_file(report, "FC-GQ-")
    assert set(gq) == {"groq_app.py", "compat_app.py"}
    assert _by_file(report, "FC-BC-") == {}


def test_project_provider_bharatcode_keeps_auto_mode(tmp_path: Path) -> None:
    report = _scan(_repo(tmp_path, "bharatcode"))
    assert _by_file(report, "FC-GQ-") == {}  # the groq client is now a conflict (unknown)
    assert "FC-BC-001" in _by_file(report, "FC-BC-")["bc_app.py"]


def test_project_provider_openai_compatible_disables_both_families(tmp_path: Path) -> None:
    report = _scan(_repo(tmp_path, "openai_compatible"))
    assert _by_file(report, "FC-GQ-") == {} and _by_file(report, "FC-BC-") == {}


def test_groq_model_alone_never_triggers_groq_rules(tmp_path: Path) -> None:
    (tmp_path / "compat_app.py").write_text(COMPAT_APP, encoding="utf-8")
    report = _scan(tmp_path)
    call = next(d for d in report.providers_detected if d.location.line == 6)
    assert call.provider == "openai_compatible_unknown"
    assert _by_file(report, "FC-GQ-") == {}


# --- confidence downgrade --------------------------------------------------------------------

UNRESOLVED = (
    "import groq\n"
    "from groq import Groq\n"
    "client = Groq(timeout=60.0)\n"
    "def ask(c, messages):\n"
    "    try:\n"
    "        return c.chat.completions.create(model='m', messages=messages, n=2)\n"
    "    except groq.APIError:\n"
    "        return None\n"
)


def _n_finding(report: Report):
    return next(f for f in report.findings if f.rule_id == "FC-GQ-008")


def test_low_provider_confidence_lowers_groq_findings_one_level(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(UNRESOLVED, encoding="utf-8")
    report = _scan(tmp_path)
    call = next(d for d in report.providers_detected if d.location.line == 6)
    assert (call.provider, call.confidence.value) == ("groq", "low")
    finding = _n_finding(report)
    assert (finding.severity.value, finding.confidence.value) == ("high", "medium")


def test_provider_groq_skips_the_downgrade(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(UNRESOLVED, encoding="utf-8")
    finding = _n_finding(_scan(tmp_path, "groq"))
    assert (finding.severity.value, finding.confidence.value) == ("high", "high")


# --- client-constructor rule (FC-GQ-015) -------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "fires"),
    [(None, False), ("groq", True), ("bharatcode", False), ("openai_compatible", False)],
)
def test_constructor_rule_gating(tmp_path: Path, override: str | None, fires: bool) -> None:
    (tmp_path / "app.py").write_text(CONFLICT_CLIENT, encoding="utf-8")
    report = _scan(tmp_path, override)
    assert any(f.rule_id == "FC-GQ-015" for f in report.findings) is fires


def test_constructor_rule_runs_on_groq_clients_in_auto_mode(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "from groq import Groq\nc = Groq(base_url='http://localhost:8000/openai/v1')\n",
        encoding="utf-8",
    )
    report = _scan(tmp_path)
    found = [f for f in report.findings if f.rule_id == "FC-GQ-015"]
    assert len(found) == 1 and found[0].provider == "groq"
