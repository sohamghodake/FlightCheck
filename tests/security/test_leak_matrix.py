"""Secret-leak matrix (plan §27 Phase 11 item 1, §20.4, §26.2 "planted secrets in outputs: 0").

Distinct fake values are planted in a source literal, in ``BHARATCODE_API_KEY`` and another
secret-named environment variable, in the app's stdout and stderr, in a recorded request body
and in AI output (the fake backend, and the BharatCode backend through an injected transport).
``scan``, ``test``, ``explain`` (fake, bharatcode, ``--dry-run``) and ``report`` (html and
terminal) run through the CLI at ``-vv``; every stdout, stderr (logs included) and written
file is checked for every raw value. ``report`` runs with the planted environment removed, so
the saved files must already be clean.

Every planted value is built by concatenation: this file never holds a complete key literal.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from typer.testing import CliRunner

from flightcheck import orchestrator
from flightcheck.cli.app import app
from flightcheck.core.redact import _registered, clear_registered_secrets, register_secret
from flightcheck.diagnostics.fake_llm import FakeLLM
from flightcheck.diagnostics.service import DiagnosticService
from flightcheck.mock.faults import MockAction, MockStep
from flightcheck.mock.payloads import responses_text
from flightcheck.reporting.json_report import REPORT_FILENAME, TRAFFIC_FILENAME

# --- planted values (obviously fake; never a complete literal in this file) -----------------
SOURCE_KEY = "bc_live_" + "LEAKSOURCE0000000001"  # a literal in the app source
ENV_KEY = "fcleak" + "envkey0000000002"  # BHARATCODE_API_KEY (matches no pattern)
OTHER_ENV = "fcleak" + "othertoken000003"  # LEAK_SERVICE_TOKEN (matches no pattern)
STDOUT_TAIL = "LEAKSTDOUT00000004"  # printed as "sk-" + tail
STDERR_TAIL = "LEAKSTDERR00000005"  # printed as "token=" + tail
BODY_TAIL = "LEAKBODY0000000006"  # sent in a request body as "bc_live_" + tail
AI_KEY = "sk-" + "LEAKAIOUTPUT000007"  # in AI output
AI_PASSWORD = "hunter" + "2LEAK0000008"  # in AI output as "password: …"
RAW_VALUES = {
    "source literal": SOURCE_KEY,
    "BHARATCODE_API_KEY": ENV_KEY,
    "LEAK_SERVICE_TOKEN": OTHER_ENV,
    "app stdout": "sk-" + STDOUT_TAIL,
    "app stderr": "token=" + STDERR_TAIL,
    "request body": "bc_live_" + BODY_TAIL,
    "AI output key": AI_KEY,
    "AI output password": AI_PASSWORD,
}

APP_SOURCE = f'''"""Leak-matrix app: plants values in source, stdout, stderr and a request body."""

import os
import sys

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=10,
)
LEGACY_KEY = "{SOURCE_KEY}"


def main() -> int:
    print("stdout carries sk-" + "{STDOUT_TAIL}")
    print("stderr carries token=" + "{STDERR_TAIL}", file=sys.stderr)
    secret = "bc_live_" + "{BODY_TAIL}"
    try:
        answer = client.responses.create(model="qwen-3.8-27b", input="key " + secret, store=False)
        print(answer.output_text)
    except Exception as exc:
        print("request failed:", type(exc).__name__, file=sys.stderr)
    return 0


def stateful() -> None:
    client.responses.create(model="qwen-3.8-27b", input="{OTHER_ENV}", store=True)


if __name__ == "__main__":
    raise SystemExit(main())
'''
TOML = '[test]\ncommand = ["python", "main.py"]\ntimeout_s = 20\n'
runner = CliRunner()


# --- AI output carrying planted values ------------------------------------------------------


def _poisoned_batch(input_text: str) -> str:
    """The fake backend's explanations with planted values in every free-text field."""
    document = json.loads(input_text)
    explanations = []
    for group in document["groups"]:
        item = FakeLLM._explain(group).model_dump()
        item["summary"] = f"Use {AI_KEY} and {ENV_KEY}."
        item["root_cause"] = f"The config says password: {AI_PASSWORD}."
        item["impact"] = f"Leaks {OTHER_ENV}."
        item["fix_steps"] = [f"Rotate {AI_KEY}.", f"Remove {SOURCE_KEY}."]
        item["caveats"] = [f"password: {AI_PASSWORD}"]
        explanations.append(item)
    return json.dumps({"explanations": explanations})


def _bharatcode_handler(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    step = MockStep(
        id="leak", action=MockAction.OK_TEXT, params={"text": _poisoned_batch(body["input"])}
    )
    status, headers, payload = responses_text(body, step, 1)
    return httpx2.Response(status, headers=headers, json=payload)


# --- running the commands -------------------------------------------------------------------


def _invoke(outputs: dict[str, str], name: str, args: list[str], codes: tuple[int, ...]) -> Any:
    result = runner.invoke(app, [*args, "-vv", "--no-color"], env={"COLUMNS": "200"})
    assert result.exit_code in codes, (name, result.output)
    outputs[f"{name} stdout"] = result.stdout
    outputs[f"{name} stderr"] = result.stderr
    return result


def _read(outputs: dict[str, str], name: str, path: Path) -> None:
    assert path.is_file(), path
    outputs[name] = path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("leak") / "app"
    root.mkdir()
    (root / "main.py").write_text(APP_SOURCE, encoding="utf-8")
    (root / "flightcheck.toml").write_text(TOML, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def planted_env() -> Iterator[pytest.MonkeyPatch]:
    """Planted environment, no network, and the redaction registry restored afterwards."""
    before = set(_registered)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FLIGHTCHECK_NO_NETWORK", "1")
        mp.setenv("BHARATCODE_API_KEY", ENV_KEY)
        mp.setenv("LEAK_SERVICE_TOKEN", OTHER_ENV)
        mp.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}")
        yield mp
    clear_registered_secrets()
    for value in before:
        register_secret(value)


def _explain_and_report(
    outputs: dict[str, str], mp: pytest.MonkeyPatch, prefix: str, report: Path, repo: Path
) -> None:
    """explain (fake, bharatcode via transport, dry run), then report (html, terminal) with
    the planted environment removed."""
    out = report.parent
    with mp.context() as inner:
        inner.setattr(FakeLLM, "respond", lambda self, request: _poisoned_batch(str(request.input)))
        _invoke(outputs, f"{prefix}explain fake", ["explain", str(report), "--llm", "fake",
                "--context", "snippets", "--repo", str(repo), "--format", "all",
                "--output", str(out / "fake.explained.json")], (0,))  # fmt: skip
    transport = httpx2.MockTransport(_bharatcode_handler)
    with mp.context() as inner:
        inner.setattr(
            orchestrator,
            "explain_service",
            lambda config, **kw: DiagnosticService(
                config, catalogue=orchestrator.check_catalogue(), transport=transport
            ),
        )
        _invoke(outputs, f"{prefix}explain bharatcode", ["explain", str(report), "--llm",
                "bharatcode", "--yes", "--context", "snippets", "--repo", str(repo),
                "--format", "all", "--output", str(out / "bc.explained.json")], (0,))  # fmt: skip
    _invoke(outputs, f"{prefix}explain dry-run", ["explain", str(report), "--llm", "bharatcode",
            "--context", "snippets", "--repo", str(repo), "--dry-run"], (0,))  # fmt: skip
    for name in ("fake", "bc"):
        _read(outputs, f"{prefix}{name} explained JSON", out / f"{name}.explained.json")
        _read(outputs, f"{prefix}{name} explained HTML", out / f"{name}.explained.html")
    with mp.context() as inner:
        inner.delenv("BHARATCODE_API_KEY")
        inner.delenv("LEAK_SERVICE_TOKEN")
        clear_registered_secrets()
        _invoke(outputs, f"{prefix}report html", ["report", str(out / "bc.explained.json"),
                "--output", str(out / "report.html")], (0,))  # fmt: skip
        _read(outputs, f"{prefix}report HTML file", out / "report.html")
        _invoke(outputs, f"{prefix}report terminal", ["report", str(out / "bc.explained.json"),
                "--format", "terminal"], (0,))  # fmt: skip


@pytest.fixture(scope="module")
def static_outputs(project: Path, planted_env: pytest.MonkeyPatch) -> dict[str, str]:
    out = project.parent / "static-out"
    outputs: dict[str, str] = {}
    _invoke(outputs, "scan", ["scan", str(project), "--format", "all", "--output", str(out)], (1,))
    _read(outputs, "scan JSON", out / REPORT_FILENAME)
    _read(outputs, "scan HTML", out / "flightcheck-report.html")
    _explain_and_report(outputs, planted_env, "", out / REPORT_FILENAME, project)
    return outputs


@pytest.fixture(scope="module")
def runtime_outputs(project: Path, planted_env: pytest.MonkeyPatch) -> dict[str, str]:
    out = project.parent / "runtime-out"
    outputs: dict[str, str] = {}
    _invoke(outputs, "test", ["test", str(project), "--allow-exec", "--keep-traffic",
            "--format", "all", "--fail-on", "none", "--output", str(out)], (0,))  # fmt: skip
    _read(outputs, "test JSON", out / REPORT_FILENAME)
    _read(outputs, "test HTML", out / "flightcheck-report.html")
    _read(outputs, "traffic file", out / TRAFFIC_FILENAME)
    _explain_and_report(outputs, planted_env, "test-report ", out / REPORT_FILENAME, project)
    return outputs


STATIC_NAMES = [
    "scan stdout", "scan stderr", "scan JSON", "scan HTML",
    "explain fake stdout", "explain fake stderr", "explain bharatcode stdout",
    "explain bharatcode stderr", "explain dry-run stdout", "explain dry-run stderr",
    "fake explained JSON", "fake explained HTML", "bc explained JSON", "bc explained HTML",
    "report html stdout", "report html stderr", "report HTML file",
    "report terminal stdout", "report terminal stderr",
]  # fmt: skip
RUNTIME_NAMES = [
    "test stdout", "test stderr", "test JSON", "test HTML", "traffic file",
    *[f"test-report {name}" for name in STATIC_NAMES[4:]],
]  # fmt: skip


def _leaks(text: str) -> list[str]:
    return [label for label, value in RAW_VALUES.items() if value in text]


# --- the matrix -----------------------------------------------------------------------------


@pytest.mark.parametrize("output", STATIC_NAMES)
def test_static_and_explain_outputs_hold_no_planted_value(
    static_outputs: dict[str, str], output: str
) -> None:
    assert _leaks(static_outputs[output]) == [], output


@pytest.mark.slow
@pytest.mark.parametrize("output", RUNTIME_NAMES)
def test_runtime_outputs_hold_no_planted_value(
    runtime_outputs: dict[str, str], output: str
) -> None:
    assert _leaks(runtime_outputs[output]) == [], output


def test_the_matrix_is_not_vacuous(static_outputs: dict[str, str]) -> None:
    """Each planted value really reached the outputs (redacted), and logging was on."""
    scan = json.loads(static_outputs["scan JSON"])
    assert {"FC-SEC-001", "FC-BC-001"} <= {f["rule_id"] for f in scan["findings"]}
    assert "bc_live_…[REDACTED]" in static_outputs["scan JSON"]  # the source literal
    assert "[REDACTED:LEAK_SERVICE_TOKEN]" in static_outputs["scan JSON"]  # FC-BC-001 excerpt
    for name in ("fake", "bc"):
        explained = json.loads(static_outputs[f"{name} explained JSON"])
        texts = [s["explanation"] for s in explained["ai_suggestions"] if s["explanation"]]
        assert texts, f"{name}: the poisoned explanations were not accepted"
        summary = texts[0]["summary"]
        assert "sk-…[REDACTED]" in summary and "REDACTED" in summary.split(" and ")[1]
        assert "password=[REDACTED]" in texts[0]["root_cause"]
    assert "FC-SEC-001" in static_outputs["explain dry-run stdout"]  # the payload was printed


@pytest.mark.slow
def test_the_runtime_matrix_is_not_vacuous(runtime_outputs: dict[str, str]) -> None:
    report = json.loads(runtime_outputs["test JSON"])
    s01 = report["scenarios"][0]
    assert s01["app_run"]["exit_code"] == 0
    assert "sk-…[REDACTED]" in s01["app_run"]["stdout_excerpt"]
    assert "token=[REDACTED]" in s01["app_run"]["stderr_excerpt"]
    traffic = json.loads(runtime_outputs["traffic file"])
    first = traffic["scenarios"][0]["exchanges"][0]
    assert first["request_json"]["input"] == "key bc_live_…[REDACTED]"
    assert first["request_headers"]["authorization"] == "[FAKE KEY]"
    assert "INFO flightcheck" in runtime_outputs["test stderr"]  # -vv logging reached stderr


def test_split_secret_limitation_is_documented() -> None:
    """§27 Phase 11 item 15: secrets split across lines are a documented limitation, not a
    caught case."""
    rules = (Path(__file__).resolve().parents[2] / "docs" / "RULES.md").read_text(encoding="utf-8")
    limitations = rules.split("## Explicit limitations", 1)[1]
    assert "**split across lines**" in limitations
    assert "Redaction is pattern- and value-based" in limitations
