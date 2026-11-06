import os
from pathlib import Path

import pytest

from flightcheck.core.errors import PathOutsideRootError
from flightcheck.core.paths import default_output_dir, resolve_inside, safe_relpath


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return project


def test_resolve_inside_accepts_relative_path(root: Path) -> None:
    assert resolve_inside(root, "pkg/app.py") == (root / "pkg" / "app.py").resolve()


def test_resolve_inside_accepts_root_itself(root: Path) -> None:
    assert resolve_inside(root, ".") == root.resolve()


def test_resolve_inside_rejects_parent_traversal(root: Path) -> None:
    with pytest.raises(PathOutsideRootError) as info:
        resolve_inside(root, "../outside.txt")
    assert info.value.exit_code == 2


def test_resolve_inside_rejects_traversal_that_reenters_elsewhere(root: Path) -> None:
    with pytest.raises(PathOutsideRootError):
        resolve_inside(root, "pkg/../../project-evil/app.py")


def test_resolve_inside_rejects_absolute_path_outside(root: Path, tmp_path: Path) -> None:
    with pytest.raises(PathOutsideRootError):
        resolve_inside(root, tmp_path / "elsewhere.py")


def test_resolve_inside_accepts_absolute_path_inside(root: Path) -> None:
    target = (root / "pkg" / "app.py").resolve()
    assert resolve_inside(root, target) == target


def test_resolve_inside_rejects_symlink_escape(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret_dir"
    outside.mkdir()
    link = root / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    with pytest.raises(PathOutsideRootError):
        resolve_inside(root, "link/file.txt")


def test_safe_relpath_is_posix(root: Path) -> None:
    assert safe_relpath(root / "pkg" / "app.py", root) == "pkg/app.py"
    assert safe_relpath(root, root) == "."


def test_safe_relpath_rejects_outside(root: Path, tmp_path: Path) -> None:
    with pytest.raises(PathOutsideRootError):
        safe_relpath(tmp_path, root)


def test_default_output_dir_creates_gitignore(root: Path) -> None:
    out = default_output_dir(root)
    assert out == (root / ".flightcheck").resolve()
    assert (out / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_default_output_dir_is_idempotent_and_keeps_existing_gitignore(root: Path) -> None:
    out = default_output_dir(root)
    (out / ".gitignore").write_text("custom\n", encoding="utf-8")
    assert default_output_dir(root) == out
    assert (out / ".gitignore").read_text(encoding="utf-8") == "custom\n"


def test_default_output_dir_for_file_target_uses_parent(root: Path) -> None:
    out = default_output_dir(root / "pkg" / "app.py")
    assert out == (root / "pkg" / ".flightcheck").resolve()
