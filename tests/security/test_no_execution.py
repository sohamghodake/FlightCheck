"""`scan`, `explain` and `report` never run repository code (plan §20.6, §27 Phase 11 item 2).

The project is built in ``tmp_path`` (not under tests/fixtures/apps, which the evaluation and
expected_runtime.json would pick up). Every file would create a sentinel file if it were
imported or run: at module level, under ``if __name__ == "__main__"``, in a package
``__init__``, in ``setup.py``, ``conftest.py`` and ``sitecustomize.py``, and through a
decorator and a class body.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from flightcheck.cli.app import app
from flightcheck.reporting.json_report import REPORT_FILENAME

runner = CliRunner()


def _writer(sentinel: Path, tag: str) -> str:
    return f"open({str(sentinel / tag)!r}, 'w').write({tag!r})\n"


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    sentinel = tmp_path / "sentinels"
    sentinel.mkdir()
    (root / "pkg").mkdir(parents=True)
    w = lambda tag: _writer(sentinel, tag)  # noqa: E731
    files = {
        "main.py": (
            "import os\nfrom openai import OpenAI\n"
            + w("module_level")
            + "client = OpenAI(base_url='https://bharatcode.ai/api/model/v1', api_key=os.environ"
            "['BHARATCODE_API_KEY'])\n"
            "def deco(fn):\n    " + w("decorator") + "    return fn\n"
            "@deco\ndef ask(q):\n"
            "    return client.responses.create(model='qwen-3.8-27b', input=q, store=True)\n"
            "class Holder:\n    " + w("class_body") + "\n"
            "if __name__ == '__main__':\n    " + w("dunder_main")
        ),
        "pkg/__init__.py": w("package_init"),
        "pkg/helpers.py": "import pkg\n" + w("helper"),
        "setup.py": w("setup_py"),
        "conftest.py": w("conftest"),
        "sitecustomize.py": w("sitecustomize"),
    }
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return root, sentinel


def _invoke(*args: str) -> Any:
    return runner.invoke(app, [*args, "--no-color"], env={"COLUMNS": "200"})


def test_scan_explain_and_report_never_execute_the_project(
    project: tuple[Path, Path], tmp_path: Path
) -> None:
    root, sentinel = project
    out = tmp_path / "out"
    result = _invoke("scan", str(root), "--output", str(out), "--format", "all", "-vv")
    assert result.exit_code == 1, result.output  # FC-BC-001 store=True is high
    assert "FC-BC-001" in result.stdout
    report = out / REPORT_FILENAME
    for args in (
        ["explain", str(report), "--llm", "fake", "--context", "snippets", "--repo", str(root)],
        ["explain", str(report), "--llm", "bharatcode", "--context", "snippets",
         "--repo", str(root), "--dry-run"],
        ["report", str(report)],
        ["report", str(report), "--format", "terminal"],
    ):  # fmt: skip
        result = _invoke(*args)
        assert result.exit_code == 0, (args, result.output)
    assert sorted(p.name for p in sentinel.iterdir()) == []


def test_groq_project_with_a_planted_key_is_never_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase G4: a groq-SDK project with GROQ_API_KEY / GROQ_BASE_URL planted in the parent
    environment. scan, explain and report run nothing, and the key reaches no output."""
    planted = "fcplanted" + "groqkey00000001"
    monkeypatch.setenv("GROQ_API_KEY", planted)
    monkeypatch.setenv("GROQ_BASE_URL", "https://groq-planted" + ".example.net")
    root, sentinel = tmp_path / "groq", tmp_path / "sentinels"
    root.mkdir()
    sentinel.mkdir()
    (root / "main.py").write_text(
        "import os\nfrom groq import Groq\n"
        + _writer(sentinel, "module_level")
        + "client = Groq(timeout=30.0)\n"
        "def ask(q):\n"
        "    return client.chat.completions.create(model='openai/gpt-oss-20b', messages=[],"
        " logprobs=True)\n"
        "if __name__ == '__main__':\n    " + _writer(sentinel, "dunder_main"),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = _invoke("scan", str(root), "--output", str(out), "--format", "all", "-vv")
    assert result.exit_code == 1, result.output  # FC-GQ-007 logprobs is high
    assert "FC-GQ-007" in result.stdout
    report = out / REPORT_FILENAME
    texts = [result.stdout, result.stderr]
    for args in (
        ["explain", str(report), "--llm", "fake", "--context", "snippets", "--repo", str(root)],
        ["report", str(report), "--format", "terminal"],
    ):
        result = _invoke(*args)
        assert result.exit_code == 0, (args, result.output)
        texts += [result.stdout, result.stderr]
    texts += [p.read_text(encoding="utf-8") for p in out.iterdir() if p.is_file()]
    assert all(planted not in text and "groq-planted" not in text for text in texts)
    assert sorted(p.name for p in sentinel.iterdir()) == []


def test_the_sentinels_do_fire_when_the_code_runs(project: tuple[Path, Path]) -> None:
    """Control: the planted code really writes sentinels when executed (not vacuous)."""
    root, sentinel = project
    code = (root / "setup.py").read_text(encoding="utf-8")
    exec(compile(code, "setup.py", "exec"), {})  # the test runs it on purpose
    assert [p.name for p in sentinel.iterdir()] == ["setup_py"]
