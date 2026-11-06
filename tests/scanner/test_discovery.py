import builtins
import os
from pathlib import Path

import pytest

from flightcheck.core.config import ScanConfig
from flightcheck.scanner import discovery
from flightcheck.scanner.discovery import DiscoveryOptions, discover, is_sensitive_filename


def write(root: Path, rel: str, content: str | bytes = "x = 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def rels(result) -> list[str]:
    return [result.relpath(p) for p in result.files]


def test_finds_python_files_sorted(tmp_path):
    write(tmp_path, "b.py")
    write(tmp_path, "a.py")
    write(tmp_path, "pkg/c.py")
    write(tmp_path, "README.md", "# hi")
    result = discover(tmp_path)
    assert rels(result) == ["a.py", "b.py", "pkg/c.py"]
    assert result.total_bytes == 3 * len("x = 1\n")
    assert result.diagnostics == []


@pytest.mark.parametrize(
    "rel",
    [
        ".venv/lib/x.py",
        "venv/x.py",
        ".git/hooks/x.py",
        "__pycache__/x.py",
        "node_modules/x.py",
        "build/x.py",
        "dist/x.py",
        "foo.egg-info/x.py",
        ".env/x.py",
        "site-packages/x.py",
    ],
)
def test_excluded_directories_are_pruned(tmp_path, rel):
    write(tmp_path, rel)
    write(tmp_path, "app.py")
    result = discover(tmp_path)
    assert rels(result) == ["app.py"]
    assert result.skipped.get("excluded_dir") == 1


@pytest.mark.parametrize(
    "name",
    [".env", ".env.local", "server.pem", "tls.key", "id_rsa", "id_ed25519.pub", "secrets.py"],
)
def test_sensitive_files_are_never_opened(tmp_path, monkeypatch, name):
    write(tmp_path, name, "SECRET=1\n")
    write(tmp_path, "app.py")
    real_open = builtins.open
    opened: list[str] = []

    def spy(file, *args, **kwargs):
        opened.append(Path(file).name)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    result = discover(tmp_path)
    assert rels(result) == ["app.py"]
    assert result.skipped.get("sensitive") == 1
    assert name not in opened


@pytest.mark.parametrize(
    ("name", "sensitive"),
    [
        ("credentials.json", True),
        ("credentials-prod.json", True),
        ("data.sqlite", True),
        ("app.db", True),
        ("cert.p12", True),
        ("env.py", False),
        ("keys.py", False),
    ],
)
def test_is_sensitive_filename(name, sensitive):
    assert is_sensitive_filename(name) is sensitive


def test_user_exclude_globs(tmp_path):
    write(tmp_path, "app.py")
    write(tmp_path, "generated/x.py")
    write(tmp_path, "tests/test_a.py")
    options = DiscoveryOptions.from_config(ScanConfig(exclude=["generated", "tests/*"]))
    result = discover(tmp_path, options)
    assert rels(result) == ["app.py"]


def test_binary_file_skipped(tmp_path):
    write(tmp_path, "blob.py", b"x = 1\x00\x01")
    write(tmp_path, "app.py")
    result = discover(tmp_path)
    assert rels(result) == ["app.py"]
    assert result.skipped["binary"] == 1


def test_file_size_limit(tmp_path):
    write(tmp_path, "big.py", "x = 1\n" * 100)
    write(tmp_path, "small.py")
    result = discover(tmp_path, DiscoveryOptions(max_file_bytes=50))
    assert rels(result) == ["small.py"]
    assert result.skipped["too_large"] == 1
    assert [d.code for d in result.diagnostics] == ["file_too_large"]
    assert result.diagnostics[0].level == "warning"


def test_file_count_limit_stops_discovery(tmp_path):
    for i in range(5):
        write(tmp_path, f"m{i}.py")
    result = discover(tmp_path, DiscoveryOptions(max_files=3))
    assert len(result.files) == 3
    assert result.skipped["limit_reached"] == 1
    assert result.diagnostics[-1].code == "discovery_limit"


def test_total_bytes_limit_stops_discovery(tmp_path):
    for i in range(5):
        write(tmp_path, f"m{i}.py", "y" * 40)
    result = discover(tmp_path, DiscoveryOptions(max_total_bytes=100))
    assert len(result.files) == 2
    assert result.total_bytes == 80
    assert result.diagnostics[-1].code == "discovery_limit"


def test_options_from_config_use_mib():
    options = DiscoveryOptions.from_config(ScanConfig())
    assert options.max_file_bytes == 1024 * 1024
    assert options.max_files == 5000
    assert options.max_total_bytes == 50 * 1024 * 1024


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_outside_root_is_ignored(tmp_path):
    outside = tmp_path / "outside"
    root = tmp_path / "root"
    write(outside, "secret_code.py")
    write(outside, "dir/inner.py")
    write(root, "app.py")
    (root / "link.py").symlink_to(outside / "secret_code.py")
    (root / "linkdir").symlink_to(outside / "dir", target_is_directory=True)
    result = discover(root)
    assert rels(result) == ["app.py"]
    assert result.skipped["outside_root"] == 1


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_inside_root_is_deduplicated(tmp_path):
    write(tmp_path, "app.py")
    (tmp_path / "alias.py").symlink_to(tmp_path / "app.py")
    result = discover(tmp_path)
    assert len(result.files) == 1


def test_single_file_target(tmp_path):
    target = write(tmp_path, "one.py")
    write(tmp_path, "other.py")
    result = discover(target)
    assert result.root == tmp_path.resolve()
    assert rels(result) == ["one.py"]


def test_single_non_python_file(tmp_path):
    target = write(tmp_path, "notes.txt", "hi")
    result = discover(target)
    assert result.files == []
    assert result.diagnostics[0].code == "not_python"


def test_missing_target(tmp_path):
    result = discover(tmp_path / "nope")
    assert result.files == []
    assert result.diagnostics[0].code == "target_missing"


def test_unreadable_file_becomes_diagnostic(tmp_path, monkeypatch):
    write(tmp_path, "locked.py")
    write(tmp_path, "app.py")
    real_open = builtins.open

    def failing(file, *args, **kwargs):
        if Path(file).name == "locked.py":
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing)
    result = discover(tmp_path)
    assert rels(result) == ["app.py"]
    assert result.skipped["unreadable"] == 1
    diag = result.diagnostics[0]
    assert diag.code == "unreadable_file"
    assert diag.path == "locked.py"


def test_walk_errors_become_diagnostics(tmp_path, monkeypatch):
    write(tmp_path, "app.py")

    def fake_walk(top, onerror=None, followlinks=False):
        assert followlinks is False
        onerror(PermissionError(13, "Permission denied", str(Path(top) / "private")))
        yield str(top), [], ["app.py"]

    monkeypatch.setattr(discovery.os, "walk", fake_walk)
    result = discover(tmp_path)
    assert rels(result) == ["app.py"]
    assert result.diagnostics[0].path == "private"
