"""Import boundaries (plan §12.2, docs/ARCHITECTURE.md §3 and §5; ADR-034).

Every module under src/flightcheck is parsed with ``ast`` (nothing is imported), and its
absolute imports are checked against the layer rules. ``if TYPE_CHECKING:`` imports count
too: a layer may not even name what it must not depend on.

The narrower boundary tests stay where they are: core (tests/unit/test_core_imports.py),
scanner exec/eval/compile (tests/scanner/test_detect.py), subprocess only in
runtime/harness.py (tests/runtime/test_harness.py) and diagnostics
(tests/diagnostics/test_context.py).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import flightcheck

SRC = Path(flightcheck.__file__).parent
ROOT = "<root>"  # `from flightcheck import __version__`: allowed everywhere

# §12.2, plus ADR-034: orchestrator → mock (it starts MockServer for `test`).
ALLOWED_INTERNAL: dict[str, set[str]] = {
    "core": set(),
    "scanner": {"core"},
    "mock": {"core"},
    "runtime": {"core", "mock"},
    "providers": {"core"},
    "diagnostics": {"core", "providers"},
    "reporting": {"core"},
    "orchestrator": {"core", "scanner", "runtime", "diagnostics", "reporting", "providers", "mock"},
    "cli": {"orchestrator", "reporting", "core"},
}
# Third-party libraries per layer (§5 table; pydantic is the model library everywhere,
# starlette is FastAPI's own base; diagnostics names httpx2 for transport injection).
ALLOWED_THIRD_PARTY: dict[str, set[str]] = {
    "core": {"pydantic"},
    "scanner": {"pydantic"},
    "mock": {"pydantic", "fastapi", "starlette", "uvicorn"},
    "runtime": {"pydantic"},
    "providers": {"pydantic", "httpx2"},
    "diagnostics": {"pydantic", "httpx2"},
    "reporting": {"pydantic", "jinja2", "rich"},
    "orchestrator": {"pydantic"},
    "cli": {"typer", "rich", "click"},
}
SCANNER_FORBIDDEN_STDLIB = {"subprocess", "importlib", "runpy", "socket", "http", "urllib"}
# `python -m flightcheck`: the console entry point is the one module allowed to import cli.
ENTRY_POINTS = {"__main__.py"}


def _layer(path: Path) -> str:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _imports(path: Path) -> list[tuple[int, str, int]]:
    """(line, module, level) for every import statement."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name, 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.lineno, node.module or "", node.level))
    return found


def _internal_target(module: str) -> str | None:
    if module == "flightcheck":
        return ROOT
    if module.startswith("flightcheck."):
        return module.split(".")[1]
    return None


MODULES = sorted(p for p in SRC.rglob("*.py") if p.name not in ENTRY_POINTS)


def test_every_layer_has_a_rule() -> None:
    layers = {_layer(p) for p in MODULES} - {"__init__"}
    assert layers == set(ALLOWED_INTERNAL), layers ^ set(ALLOWED_INTERNAL)


def _violations(path: Path, src: Path = SRC) -> list[str]:
    rel = path.relative_to(src)
    layer = rel.parts[0] if len(rel.parts) > 1 else rel.stem
    if layer == "__init__":  # src/flightcheck/__init__.py: version only
        modules = [m for _, m, _ in _imports(path)]
        return [] if modules in ([], ["__future__"]) else [f"__init__.py imports {modules}"]
    offenders = []
    for line, module, level in _imports(path):
        where = f"{rel}:{line}"
        if level:
            offenders.append(f"{where}: relative import (the checks need absolute names)")
            continue
        target = _internal_target(module)
        if target is not None:
            if target not in (layer, ROOT) and target not in ALLOWED_INTERNAL[layer]:
                offenders.append(f"{where}: {layer} → {target} ({module})")
            continue
        top = module.split(".")[0]
        if top in sys.stdlib_module_names or top == "__future__":
            if layer == "scanner" and top in SCANNER_FORBIDDEN_STDLIB:
                offenders.append(f"{where}: scanner imports {module}")
            continue
        if top not in ALLOWED_THIRD_PARTY[layer]:
            offenders.append(f"{where}: {layer} imports third-party {module}")
    return offenders


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(SRC)))
def test_module_respects_the_layer_rules(path: Path) -> None:
    assert _violations(path) == []


def test_no_module_imports_cli() -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{line}"
        for path in MODULES
        if _layer(path) != "cli"
        for line, module, _ in _imports(path)
        if _internal_target(module) == "cli"
    ]
    assert offenders == []


def test_scanner_imports_no_providers_runtime_or_execution_modules() -> None:
    """§12.2 / §20.6: `scan` stays network- and execution-free by construction."""
    offenders = []
    for path in sorted((SRC / "scanner").rglob("*.py")):
        for line, module, _ in _imports(path):
            top = module.split(".")[0]
            if _internal_target(module) in {"providers", "runtime", "mock", "diagnostics"}:
                offenders.append(f"{path.name}:{line}: {module}")
            elif top in SCANNER_FORBIDDEN_STDLIB:
                offenders.append(f"{path.name}:{line}: {module}")
    assert offenders == []


def test_the_rules_catch_a_forbidden_import(tmp_path: Path) -> None:
    """Control: forbidden imports in a scanner module and in reporting are reported."""
    src = tmp_path / "flightcheck"
    bad = src / "scanner" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "import subprocess\nfrom flightcheck.providers import http\nfrom . import x\n"
        "import httpx2\n",
        encoding="utf-8",
    )
    found = _violations(bad, src)
    assert found == [
        "scanner/bad.py:1: scanner imports subprocess",
        "scanner/bad.py:2: scanner → providers (flightcheck.providers)",
        "scanner/bad.py:3: relative import (the checks need absolute names)",
        "scanner/bad.py:4: scanner imports third-party httpx2",
    ]
    report = src / "reporting" / "x.py"
    report.parent.mkdir()
    report.write_text("from flightcheck.cli.app import app\nimport flightcheck\n")
    assert _violations(report, src) == ["reporting/x.py:1: reporting → cli (flightcheck.cli.app)"]
