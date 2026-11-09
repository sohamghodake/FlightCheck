"""Path traversal (plan §20.9, §27 Phase 11 item 3).

Every attempt really tries to escape (``..``, absolute paths, symlinks) and must end in exit 2
or a recorded rejection, with nothing read from or written outside the allowed directory.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from flightcheck import orchestrator
from flightcheck.cli.app import app
from flightcheck.diagnostics.fake_llm import FakeLLM
from flightcheck.mock.server import MockServer
from flightcheck.reporting.json_report import REPORT_FILENAME
from flightcheck.runtime import harness

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "apps" / "bc_bad_no_resilience"
OUTSIDE_TEXT = "outside-" + "MARKER-0042"  # must never be read into any output
runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A directory outside every root, holding a file that must stay untouched."""
    out = tmp_path / "outside"
    out.mkdir()
    (out / "victim.txt").write_text(OUTSIDE_TEXT, encoding="utf-8")
    (out / "outside.py").write_text(f'SECRET = "{OUTSIDE_TEXT}"\n', encoding="utf-8")
    return out


def _untouched(outside: Path) -> None:
    assert (outside / "victim.txt").read_text(encoding="utf-8") == OUTSIDE_TEXT
    assert sorted(p.name for p in outside.iterdir()) == ["outside.py", "victim.txt"]


def _invoke(*args: str) -> Any:
    return runner.invoke(app, [*args, "--no-color"], env={"COLUMNS": "200"})


def _scan_report(project: Path, out: Path) -> Path:
    result = _invoke("scan", str(project), "--output", str(out), "--format", "json")
    assert result.exit_code == 0, result.output  # medium findings only (default --fail-on high)
    return out / REPORT_FILENAME


# --- --output ---------------------------------------------------------------------------------


def test_scan_output_dir_with_a_report_symlinked_outside(
    project: Path, outside: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / REPORT_FILENAME).symlink_to(outside / "victim.txt")
    result = _invoke("scan", str(project), "--output", str(out))
    assert result.exit_code == 2, result.output
    assert "outside the allowed directory" in result.stderr
    _untouched(outside)


def test_scan_html_symlinked_outside(project: Path, outside: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "flightcheck-report.html").symlink_to(outside / "victim.txt")
    result = _invoke("scan", str(project), "--output", str(out), "--format", "html")
    assert result.exit_code == 2, result.output
    _untouched(outside)


def test_scan_default_output_dir_symlinked_outside(project: Path, outside: Path) -> None:
    (project / ".flightcheck").symlink_to(outside, target_is_directory=True)
    result = _invoke("scan", str(project))
    assert result.exit_code == 2, result.output
    _untouched(outside)


def test_explain_output_symlinked_outside(project: Path, outside: Path, tmp_path: Path) -> None:
    report = _scan_report(project, tmp_path / "out")
    link = tmp_path / "out" / "explained.json"
    link.symlink_to(outside / "victim.txt")
    result = _invoke("explain", str(report), "--llm", "fake", "--output", str(link))
    assert result.exit_code == 2, result.output
    _untouched(outside)


def test_explain_html_symlinked_outside(project: Path, outside: Path, tmp_path: Path) -> None:
    report = _scan_report(project, tmp_path / "out")
    (tmp_path / "out" / "flightcheck-report.explained.html").symlink_to(outside / "victim.txt")
    result = _invoke("explain", str(report), "--llm", "fake", "--format", "html")
    assert result.exit_code == 2, result.output
    _untouched(outside)


def test_report_output_symlinked_outside(project: Path, outside: Path, tmp_path: Path) -> None:
    report = _scan_report(project, tmp_path / "out")
    link = tmp_path / "out" / "r.html"
    link.symlink_to(outside / "victim.txt")
    result = _invoke("report", str(report), "--output", str(link))
    assert result.exit_code == 2, result.output
    assert "outside the allowed directory" in result.stderr
    _untouched(outside)


# --- [test].cwd -------------------------------------------------------------------------------


@pytest.fixture
def no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not start a mock server or a process")

    monkeypatch.setattr(MockServer, "start", boom)
    monkeypatch.setattr(harness.subprocess, "Popen", boom)
    monkeypatch.setattr(orchestrator, "run_app", boom)


@pytest.mark.parametrize("cwd", ["..", "../outside", "/tmp", "link"])
def test_test_cwd_escaping_the_project_exits_2(
    project: Path, outside: Path, tmp_path: Path, no_execution: None, cwd: str
) -> None:
    (project / "link").symlink_to(outside, target_is_directory=True)
    (project / "flightcheck.toml").write_text(
        f'[test]\ncommand = ["python", "main.py", "hi"]\ncwd = "{cwd}"\n', encoding="utf-8"
    )
    result = _invoke("test", str(project), "--allow-exec", "--output", str(tmp_path / "o"))
    assert result.exit_code == 2, result.output
    assert "outside the allowed directory" in result.stderr
    assert "Your code will run" not in result.stderr
    _untouched(outside)


# --- snippets under --repo --------------------------------------------------------------------


def _retarget(report: Path, new_path: str) -> Path:
    """The same report with every finding's location (and source evidence) moved to
    ``new_path``: what a tampered or foreign report would ask explain to read."""
    data = json.loads(report.read_text(encoding="utf-8"))
    for finding in data["findings"]:
        if finding["location"]:
            finding["location"]["path"] = new_path
    target = report.with_name("tampered.json")
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


@pytest.mark.parametrize("kind", ["dotdot", "absolute", "symlink"])
def test_snippet_paths_outside_repo_are_never_read(
    project: Path, outside: Path, tmp_path: Path, kind: str
) -> None:
    report = _scan_report(project, tmp_path / "out")
    (project / "link.py").symlink_to(outside / "outside.py")
    path = {
        "dotdot": "../outside/outside.py",
        "absolute": str(outside / "outside.py"),
        "symlink": "link.py",
    }[kind]
    tampered = _retarget(report, path)
    result = _invoke(
        "explain", str(tampered), "--llm", "bharatcode", "--context", "snippets",
        "--repo", str(project), "--dry-run",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert OUTSIDE_TEXT not in result.output
    assert "snippet omitted" in result.stderr
    _untouched(outside)


# --- AI patch paths ---------------------------------------------------------------------------


@pytest.mark.parametrize("patch_path", ["../outside/outside.py", "other.py", "/etc/hosts"])
def test_ai_patch_for_a_path_that_was_not_sent_is_rejected(
    project: Path, outside: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_path: str
) -> None:
    report = _scan_report(project, tmp_path / "out")

    def respond(self: FakeLLM, request: Any) -> str:
        document = json.loads(str(request.input))
        items = []
        for group in document["groups"]:
            item = FakeLLM._explain(group).model_dump()
            item["patch"] = {
                "path": patch_path,
                "original_snippet": "client = OpenAI(",
                "proposed_snippet": f"# {OUTSIDE_TEXT}",
            }
            items.append(item)
        return json.dumps({"explanations": items})

    monkeypatch.setattr(FakeLLM, "respond", respond)
    out_json = tmp_path / "out" / "x.explained.json"
    result = _invoke(
        "explain", str(report), "--llm", "fake", "--context", "snippets",
        "--repo", str(project), "--output", str(out_json),
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    explained = json.loads(out_json.read_text(encoding="utf-8"))
    suggestions = explained["ai_suggestions"]
    assert suggestions and all(s["explanation"] is None for s in suggestions)
    assert {s["fallback_reason"] for s in suggestions} == {"rejected"}
    assert {s["rejected_reason"] for s in suggestions} == {"patch path was not sent with a snippet"}
    assert all(s["diff"] is None for s in suggestions)
    assert OUTSIDE_TEXT not in out_json.read_text(encoding="utf-8")
    _untouched(outside)
    assert (project / "main.py").read_text(encoding="utf-8") == (FIXTURE / "main.py").read_text(
        encoding="utf-8"
    )
