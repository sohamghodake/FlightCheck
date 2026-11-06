"""core may import only the standard library, pydantic and other core modules."""

import ast
import sys
from pathlib import Path

import flightcheck.core

CORE_DIR = Path(flightcheck.core.__file__).parent
ALLOWED_THIRD_PARTY = {"pydantic"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_core_imports_only_stdlib_pydantic_and_core() -> None:
    offenders = []
    for path in sorted(CORE_DIR.glob("*.py")):
        for module in _imported_modules(path):
            top = module.split(".")[0]
            if top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY:
                continue
            if module == "flightcheck.core" or module.startswith("flightcheck.core."):
                continue
            offenders.append(f"{path.name}: {module}")
    assert offenders == []
