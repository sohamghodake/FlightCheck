"""Safe path handling: nothing FlightCheck reads or writes may escape its root."""

from __future__ import annotations

import os
from pathlib import Path

from flightcheck.core.errors import PathOutsideRootError

OUTPUT_DIR_NAME = ".flightcheck"


def resolve_inside(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> Path:
    """Resolve ``candidate`` (relative to ``root`` unless absolute) and require it inside root.

    Symlinks are resolved first, so a link pointing outside the root is rejected.
    """
    root_resolved = Path(root).resolve()
    path = Path(candidate)
    if not path.is_absolute():
        path = root_resolved / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise PathOutsideRootError(
            f"Path is outside the allowed directory: {candidate}",
            hint=f"Use a path inside {root_resolved.name}/.",
        )
    return resolved


def safe_relpath(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> str:
    """Return ``path`` relative to ``root`` as a POSIX string (``"."`` for the root itself)."""
    resolved = resolve_inside(root, path)
    return resolved.relative_to(Path(root).resolve()).as_posix()


def default_output_dir(target: str | os.PathLike[str]) -> Path:
    """Return ``<target>/.flightcheck/``, creating it with a ``.gitignore`` containing ``*``.

    If ``target`` is a file, the directory is created next to it.
    """
    base = Path(target)
    if base.is_file():
        base = base.parent
    out = resolve_inside(base, OUTPUT_DIR_NAME)
    out.mkdir(parents=True, exist_ok=True)
    gitignore = out / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    return out
