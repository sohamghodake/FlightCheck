"""Safe repository discovery (plan §20.3).

Walks the target with ``os.walk(followlinks=False)``, prunes excluded directories in place,
never opens sensitive filenames, reads only ``*.py`` files, skips binaries (NUL byte in the
first 8 KiB), enforces size/count/total limits and checks every file with ``resolve_inside``.
Discovery never raises for unreadable files: problems become :class:`Diagnostic` objects.
"""

from __future__ import annotations

import fnmatch
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from flightcheck.core.config import ScanConfig
from flightcheck.core.errors import PathOutsideRootError
from flightcheck.core.models import Diagnostic
from flightcheck.core.paths import resolve_inside, safe_relpath

BINARY_SNIFF_BYTES = 8192

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        "node_modules",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "site-packages",
        ".eggs",
        ".idea",
        ".vscode",
    }
)
SKIP_DIR_GLOBS = ("*.egg-info",)

SENSITIVE_FILE_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "credentials*.json",
    "secrets.*",
    "*.sqlite",
    "*.db",
)

# Reasons used as keys of DiscoveryResult.skipped (and RepoMetadata.files_skipped).
SKIP_EXCLUDED_DIR = "excluded_dir"
SKIP_EXCLUDED = "excluded"
SKIP_SENSITIVE = "sensitive"
SKIP_OUTSIDE_ROOT = "outside_root"
SKIP_TOO_LARGE = "too_large"
SKIP_BINARY = "binary"
SKIP_UNREADABLE = "unreadable"
SKIP_LIMIT = "limit_reached"


@dataclass(frozen=True)
class DiscoveryOptions:
    exclude: tuple[str, ...] = ()
    max_file_bytes: int = ScanConfig().max_file_bytes
    max_files: int = ScanConfig().max_files
    max_total_bytes: int = ScanConfig().max_total_bytes

    @classmethod
    def from_config(cls, scan: ScanConfig) -> DiscoveryOptions:
        return cls(
            exclude=tuple(scan.exclude),
            max_file_bytes=scan.max_file_bytes,
            max_files=scan.max_files,
            max_total_bytes=scan.max_total_bytes,
        )


@dataclass
class DiscoveryResult:
    root: Path  # resolved scan root (the directory, or the parent of a single file)
    files: list[Path] = field(default_factory=list)  # resolved, sorted, inside root
    skipped: dict[str, int] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    total_bytes: int = 0

    def relpath(self, path: Path) -> str:
        return safe_relpath(path, self.root)


def is_sensitive_filename(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_FILE_GLOBS)


def _is_skipped_dir(name: str) -> bool:
    return name in SKIP_DIRS or any(fnmatch.fnmatch(name, g) for g in SKIP_DIR_GLOBS)


def _matches_user_exclude(relpath: str, name: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        stripped = pattern.rstrip("/")
        if (
            fnmatch.fnmatch(relpath, stripped)
            or fnmatch.fnmatch(name, stripped)
            or fnmatch.fnmatch(relpath, stripped + "/*")
        ):
            return True
    return False


class _Walker:
    def __init__(self, root: Path, options: DiscoveryOptions) -> None:
        self.root = root
        self.options = options
        self.result = DiscoveryResult(root=root)
        self.skipped: Counter[str] = Counter()
        self.seen: set[Path] = set()
        self.stopped = False

    def diag(self, level: str, code: str, message: str, path: str | None = None) -> None:
        self.result.diagnostics.append(
            Diagnostic(level=level, code=code, message=message, path=path)  # type: ignore[arg-type]
        )

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def consider(self, path: Path) -> None:
        """Apply every file-level check to one candidate path (not yet opened)."""
        name = path.name
        rel = self.rel(path)
        if is_sensitive_filename(name):
            self.skipped[SKIP_SENSITIVE] += 1
            return
        if not name.endswith(".py"):
            return
        if _matches_user_exclude(rel, name, self.options.exclude):
            self.skipped[SKIP_EXCLUDED] += 1
            return
        try:
            resolved = resolve_inside(self.root, path)
        except PathOutsideRootError:
            self.skipped[SKIP_OUTSIDE_ROOT] += 1
            self.diag(
                "info", "path_outside_root", "Skipped a link that points outside the root.", rel
            )
            return
        except OSError as exc:
            self._unreadable(rel, exc)
            return
        if resolved in self.seen:
            return
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            self._unreadable(rel, exc)
            return
        if not resolved.is_file():
            return
        if size > self.options.max_file_bytes:
            self.skipped[SKIP_TOO_LARGE] += 1
            self.diag(
                "warning",
                "file_too_large",
                f"Skipped: {size} bytes exceeds the {self.options.max_file_bytes}-byte file limit.",
                rel,
            )
            return
        if len(self.result.files) >= self.options.max_files:
            self._stop(f"file limit of {self.options.max_files} files reached")
            return
        if self.result.total_bytes + size > self.options.max_total_bytes:
            self._stop(f"total size limit of {self.options.max_total_bytes} bytes reached")
            return
        try:
            with open(resolved, "rb") as handle:
                head = handle.read(BINARY_SNIFF_BYTES)
        except OSError as exc:
            self._unreadable(rel, exc)
            return
        if b"\x00" in head:
            self.skipped[SKIP_BINARY] += 1
            self.diag("info", "binary_file", "Skipped: file looks binary (NUL byte).", rel)
            return
        self.seen.add(resolved)
        self.result.files.append(resolved)
        self.result.total_bytes += size

    def _unreadable(self, rel: str, exc: OSError) -> None:
        self.skipped[SKIP_UNREADABLE] += 1
        reason = exc.strerror or type(exc).__name__
        self.diag("warning", "unreadable_file", f"Skipped: could not read file ({reason}).", rel)

    def _stop(self, why: str) -> None:
        self.stopped = True
        self.skipped[SKIP_LIMIT] += 1
        self.diag(
            "warning",
            "discovery_limit",
            f"Discovery stopped: {why}; remaining files were not scanned.",
        )

    def walk(self) -> None:
        def on_error(exc: OSError) -> None:
            path = Path(exc.filename) if exc.filename else self.root
            self._unreadable(self.rel(path), exc)

        for dirpath, dirnames, filenames in os.walk(self.root, onerror=on_error, followlinks=False):
            current = Path(dirpath)
            kept: list[str] = []
            for dirname in sorted(dirnames):
                rel = self.rel(current / dirname)
                if _is_skipped_dir(dirname) or _matches_user_exclude(
                    rel, dirname, self.options.exclude
                ):
                    self.skipped[SKIP_EXCLUDED_DIR] += 1
                    continue
                kept.append(dirname)
            dirnames[:] = kept  # in-place pruning: os.walk will not descend into the rest
            for filename in sorted(filenames):
                self.consider(current / filename)
                if self.stopped:
                    return


def discover(
    target: str | os.PathLike[str], options: DiscoveryOptions | None = None
) -> DiscoveryResult:
    """Return the Python files to scan under ``target`` (a directory or a single ``.py`` file)."""
    options = options or DiscoveryOptions()
    target_path = Path(target)
    if target_path.is_file():
        root = target_path.resolve().parent
        walker = _Walker(root, options)
        if not target_path.name.endswith(".py"):
            walker.diag("warning", "not_python", "Target is not a .py file.", target_path.name)
        else:
            walker.consider(root / target_path.name)
    else:
        root = target_path.resolve()
        walker = _Walker(root, options)
        if not root.is_dir():
            walker.diag("error", "target_missing", "Target does not exist or is not a directory.")
        else:
            walker.walk()
    result = walker.result
    result.files.sort()
    result.skipped = dict(sorted(walker.skipped.items()))
    return result
