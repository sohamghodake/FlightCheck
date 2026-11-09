"""Harness: consent, scrubbed child environment, process control, output handling (Phase 7A)."""

from __future__ import annotations

import ast
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from flightcheck.core.config import TestConfig
from flightcheck.core.errors import ConsentRequiredError
from flightcheck.mock.server import MockServer
from flightcheck.runtime import harness
from flightcheck.runtime.harness import (
    FAKE_KEY_PREFIX,
    OUTPUT_LIMIT_BYTES,
    AppLaunchError,
    build_child_env,
    ensure_consent,
    make_fake_key,
    run_app,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "flightcheck"
RUNTIME_DIR = SRC / "runtime"
POSIX = os.name != "nt"

PLANTED = {
    "BHARATCODE_API_KEY": "bc_live_REALKEY0000000000000001",
    "OPENAI_API_KEY": "sk-realopenaikey0000000000001",
    "AWS_SECRET_ACCESS_KEY": "awsSecretPlanted0000000000001",
    "AWS_ACCESS_KEY_ID": "AKIAPLANTED000000001",
    "GITHUB_TOKEN": "ghp_planted00000000000000000000001",
    "HTTP_PROXY": "http://corp-proxy.example:3128",
    "https_proxy": "http://corp-proxy.example:3128",
    "ALL_PROXY": "socks5://corp-proxy.example:1080",
    "no_proxy": "*",
}


def _parent_env(**extra: str) -> dict[str, str]:
    """A parent env whose PATH starts with the interpreter that has openai installed."""
    env = {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        **PLANTED,
    }
    env.update(extra)
    return env


def _config(**overrides: object) -> TestConfig:
    return TestConfig(command=["python", "main.py"], **overrides)


def _write(tmp_path: Path, name: str, code: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return path


# --- consent and keys ---------------------------------------------------------------------


def test_ensure_consent_refuses_without_allow_exec() -> None:
    with pytest.raises(ConsentRequiredError) as info:
        ensure_consent(False, target="./app")
    assert info.value.exit_code == 2
    assert info.value.hint == "flightcheck test ./app --allow-exec"


def test_ensure_consent_requires_exactly_true() -> None:
    ensure_consent(True)
    with pytest.raises(ConsentRequiredError):
        ensure_consent(1)  # type: ignore[arg-type]


def test_fake_keys_are_fresh_and_marked() -> None:
    keys = {make_fake_key() for _ in range(20)}
    assert len(keys) == 20
    assert all(k.startswith(FAKE_KEY_PREFIX) and len(k) > len(FAKE_KEY_PREFIX) + 16 for k in keys)


# --- build_child_env ----------------------------------------------------------------------


def test_child_env_drops_everything_outside_the_allowlist() -> None:
    absolute_path = os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])
    parent = _parent_env(SOME_OTHER="x", PYTHONSTARTUP="/evil.py", PATH=absolute_path)
    env = build_child_env(parent, _config(), "http://127.0.0.1:9/api/model/v1", "fc_fake_key_1")
    for name, value in PLANTED.items():
        if name.upper() in ("HTTP_PROXY",):
            continue  # replaced by the egress guard, checked below
        assert value not in env.values(), name
    assert "SOME_OTHER" not in env and "PYTHONSTARTUP" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env and "GITHUB_TOKEN" not in env
    assert env["PATH"] == parent["PATH"] and env["HOME"] == parent["HOME"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert "PYTHONPATH" not in env


def test_child_env_makes_relative_path_entries_absolute(tmp_path: Path) -> None:
    """Phase 7B fix: `PATH=.venv/bin:$PATH flightcheck test APP` names .venv/bin relative to
    where the user ran FlightCheck, not to [test].cwd, where the child starts."""
    parent = _parent_env(PATH=os.pathsep.join([".venv/bin", "/usr/bin", "", "bin"]))
    env = build_child_env(parent, _config(), "http://u", "k", parent_cwd=tmp_path)
    assert env["PATH"].split(os.pathsep) == [
        str(tmp_path / ".venv/bin"),
        "/usr/bin",
        "",
        str(tmp_path / "bin"),
    ]


def test_child_env_sets_overrides_for_every_listed_variable() -> None:
    config = _config(base_url_env=["A_URL", "B_URL"], api_key_env=["A_KEY", "B_KEY"])
    env = build_child_env(_parent_env(), config, "http://127.0.0.1:5/api/model/v1", "fc_fake_key_z")
    assert env["A_URL"] == env["B_URL"] == "http://127.0.0.1:5/api/model/v1"
    assert env["A_KEY"] == env["B_KEY"] == "fc_fake_key_z"
    default = build_child_env(_parent_env(), _config(), "http://u", "fc_fake_key_y")
    assert default["BHARATCODE_BASE_URL"] == default["OPENAI_BASE_URL"] == "http://u"
    assert default["BHARATCODE_API_KEY"] == default["OPENAI_API_KEY"] == "fc_fake_key_y"


def test_child_env_keeps_pythonpath_only_when_set() -> None:
    env = build_child_env(_parent_env(PYTHONPATH="/x"), _config(), "http://u", "k")
    assert env["PYTHONPATH"] == "/x"
    env = build_child_env(_parent_env(PYTHONPATH=""), _config(), "http://u", "k")
    assert "PYTHONPATH" not in env


def test_egress_guard_replaces_inherited_proxies_in_any_case() -> None:
    env = build_child_env(_parent_env(), _config(), "http://u", "k", guard_port=4321)
    proxy_names = {n for n in env if n.upper().endswith("_PROXY")}
    assert proxy_names == {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"] == "http://127.0.0.1:4321"
    assert env["NO_PROXY"] == "127.0.0.1,localhost"


def test_egress_guard_cannot_be_overridden_by_test_config() -> None:
    config = _config(base_url_env=["HTTPS_PROXY", "no_proxy"], api_key_env=["NO_PROXY"])
    env = build_child_env(_parent_env(), config, "http://u", "k", guard_port=4321)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:4321"
    assert env["NO_PROXY"] == "127.0.0.1,localhost"
    assert "no_proxy" not in env


def test_guard_port_defaults_to_an_unused_local_port() -> None:
    env = build_child_env(_parent_env(), _config(), "http://u", "k")
    port = int(env["HTTP_PROXY"].rsplit(":", 1)[1])
    assert 0 < port < 65536


# --- output handling (no processes) -------------------------------------------------------


def test_excerpt_replaces_invalid_utf8_and_redacts() -> None:
    text = harness._excerpt(b"ok \xff\xfe key=bc_live_ABCDEFGH1234 done")
    assert "�" in text and "bc_live_ABCDEFGH1234" not in text and "[REDACTED]" in text


def test_excerpt_truncates_to_8_kb_after_redaction() -> None:
    raw = ("bc_live_ABCDEFGH1234 " + "é" * 10_000).encode("utf-8")
    text = harness._excerpt(raw)
    assert len(text.encode("utf-8")) <= OUTPUT_LIMIT_BYTES
    assert text.endswith(harness.TRUNCATION_MARK)
    assert "bc_live_ABCDEFGH1234" not in text


def test_excerpt_applies_path_aliases_longest_first() -> None:
    text = harness._excerpt(
        b"/home/u/proj/app.py and /home/u/x", {"/home/u": "~", "/home/u/proj": "<proj>"}
    )
    assert text == "<proj>/app.py and ~/x"


def test_run_app_rejects_bad_commands_before_spawning(tmp_path: Path) -> None:
    with pytest.raises(AppLaunchError):
        run_app([], tmp_path, {}, "", 5)
    with pytest.raises(AppLaunchError):
        run_app(["python", ""], tmp_path, {}, "", 5)


# --- run_app with real child processes ----------------------------------------------------


@pytest.mark.slow
def test_child_sees_only_the_scrubbed_env_and_secrets_never_reach_apprun(tmp_path: Path) -> None:
    script = _write(
        tmp_path,
        "show_env.py",
        """
        import os
        for name in sorted(os.environ):
            print(f"{name}={os.environ[name]}")
        print("planted: bc_live_PRINTEDBYCHILD0001")
        """,
    )
    parent = _parent_env()
    env = build_child_env(parent, _config(), "http://127.0.0.1:1/api/model/v1", "fc_fake_key_t")
    run = run_app(["python", script.name, "bc_live_INARGV00000001"], tmp_path, env, "", 20)
    assert run.exit_code == 0, run.stderr_excerpt
    names = {line.split("=", 1)[0] for line in run.stdout_excerpt.splitlines() if "=" in line}
    allowed = set(harness.ENV_ALLOWLIST) | {
        "PYTHONIOENCODING", "BHARATCODE_BASE_URL", "OPENAI_BASE_URL", "BHARATCODE_API_KEY",
        "OPENAI_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    }  # fmt: skip
    # The OS may add a few process variables (e.g. __CF_USER_TEXT_ENCODING on macOS).
    extra = {n for n in names - allowed if not n.startswith("__")}
    assert extra <= {"LC_CTYPE", "PWD", "SHLVL", "_"}, extra
    dumped = run.model_dump_json()
    for value in PLANTED.values():
        if "corp-proxy" in value or value == "*":
            continue
        assert value not in dumped
    assert "bc_live_PRINTEDBYCHILD0001" not in run.stdout_excerpt
    assert "bc_live_INARGV00000001" not in " ".join(run.command)
    # The child got the fake key (shown redacted: the api_key=... pattern masks any value).
    assert "OPENAI_API_KEY=" in run.stdout_excerpt and "BHARATCODE_BASE_URL=http://127.0.0.1:1" in (
        run.stdout_excerpt
    )


@pytest.mark.slow
def test_stdin_is_closed_so_a_reading_app_gets_eof(tmp_path: Path) -> None:
    script = _write(tmp_path, "read.py", "import sys\nprint(repr(sys.stdin.read()))\n")
    env = build_child_env(_parent_env(), _config(), "http://u", "k")
    run = run_app(["python", script.name], tmp_path, env, "", 10)
    assert (run.exit_code, run.timed_out, run.stdout_excerpt.strip()) == (0, False, "''")
    run = run_app(["python", script.name], tmp_path, env, "line 1\nline 2\n", 10)
    assert run.stdout_excerpt.strip() == repr("line 1\nline 2\n")


@pytest.mark.slow
def test_non_utf8_output_and_exit_codes_are_captured(tmp_path: Path) -> None:
    script = _write(
        tmp_path,
        "bytes.py",
        """
        import sys
        sys.stdout.buffer.write(b"caf\\xe9 \\xff\\n")
        sys.stderr.write("bad things\\n")
        sys.exit(7)
        """,
    )
    env = build_child_env(_parent_env(), _config(), "http://u", "k")
    run = run_app(["python", script.name], tmp_path, env, "", 10)
    assert run.exit_code == 7 and not run.timed_out
    assert run.stdout_excerpt == "caf� �\n"
    assert run.stderr_excerpt == "bad things\n"


@pytest.mark.slow
def test_huge_output_is_capped(tmp_path: Path) -> None:
    script = _write(tmp_path, "loud.py", "import sys\nsys.stdout.write('x' * 5_000_000)\n")
    env = build_child_env(_parent_env(), _config(), "http://u", "k")
    run = run_app(["python", script.name], tmp_path, env, "", 20)
    assert run.exit_code == 0
    assert len(run.stdout_excerpt.encode("utf-8")) <= OUTPUT_LIMIT_BYTES


@pytest.mark.slow
def test_missing_command_is_a_launch_error_with_a_hint(tmp_path: Path) -> None:
    env = build_child_env(_parent_env(), _config(), "http://u", "k")
    with pytest.raises(AppLaunchError) as info:
        run_app(["flightcheck-no-such-binary-xyz", "a"], tmp_path, env, "", 5)
    assert "not found" in info.value.message
    assert info.value.hint is not None and "[test].command" in info.value.hint


@pytest.mark.slow
@pytest.mark.skipif(not POSIX, reason="process groups are POSIX-only")
def test_timeout_kills_the_child_and_its_grandchildren(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    script = _write(
        tmp_path,
        "spawner.py",
        f"""
        import subprocess, time
        child = subprocess.Popen(["sleep", "60"])
        open({str(pid_file)!r}, "w").write(str(child.pid))
        time.sleep(60)
        """,
    )
    env = build_child_env(_parent_env(), _config(), "http://u", "k")
    timeout_s = 1.5
    started = time.monotonic()
    run = run_app(["python", script.name], tmp_path, env, "", timeout_s)
    assert run.timed_out is True
    assert run.exit_code is not None and run.exit_code != 0
    grandchild = int(pid_file.read_text())
    deadline = started + timeout_s + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("grandchild still alive after timeout + 5 s")
    assert time.monotonic() - started < timeout_s + 5


@pytest.mark.slow
def test_abort_when_stops_the_app_early(tmp_path: Path) -> None:
    script = _write(tmp_path, "sleepy.py", "import time\ntime.sleep(30)\n")
    env = build_child_env(_parent_env(), _config(), "http://u", "k")
    started = time.monotonic()
    run = run_app(["python", script.name], tmp_path, env, "", 20, abort_when=lambda: True)
    assert time.monotonic() - started < 10
    assert run.timed_out is False and run.exit_code != 0


@pytest.mark.slow
def test_egress_guard_lets_the_sdk_reach_the_mock_and_blocks_other_hosts(tmp_path: Path) -> None:
    """httpx2 (trust_env) and the OpenAI SDK honour NO_PROXY for 127.0.0.1; a non-local host
    goes to the dead proxy and fails at once (no DNS lookup, nothing leaves the machine)."""
    script = _write(
        tmp_path,
        "egress.py",
        """
        import os, time
        import openai
        from openai import OpenAI
        local = OpenAI(base_url=os.environ["BHARATCODE_BASE_URL"],
                       api_key=os.environ["BHARATCODE_API_KEY"], max_retries=0)
        answer = local.responses.create(model="qwen-3.8-27b", input="hi", store=False)
        print("LOCAL", answer.output_text)
        far = OpenAI(base_url="https://flightcheck-egress-test.invalid/v1", api_key="x",
                     max_retries=0, timeout=10)
        started = time.monotonic()
        try:
            far.responses.create(model="qwen-3.8-27b", input="hi", store=False)
            print("REMOTE_REACHED")
        except openai.APIConnectionError as exc:
            took = round(time.monotonic() - started, 1)
            print("REMOTE_BLOCKED", type(exc.__cause__).__name__, took)
        """,
    )  # fmt: skip
    key = make_fake_key()
    with MockServer(fake_key=key) as server:
        env = build_child_env(_parent_env(), _config(), server.base_url, key)
        run = run_app(["python", script.name], tmp_path, env, "", 20)
    assert run.exit_code == 0, run.stderr_excerpt
    assert "LOCAL FlightCheck mock answer" in run.stdout_excerpt
    assert "REMOTE_REACHED" not in run.stdout_excerpt
    blocked = [
        line for line in run.stdout_excerpt.splitlines() if line.startswith("REMOTE_BLOCKED")
    ]
    assert blocked and float(blocked[0].split()[-1]) < 5
    assert len(server.recorder) == 1


# --- architecture -------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_runtime_imports_only_core_mock_and_runtime() -> None:
    files = sorted(RUNTIME_DIR.glob("*.py"))
    assert {p.name for p in files} >= {"__init__.py", "harness.py", "scenarios.py", "validators.py"}
    for path in files:
        for name in _imports(path):
            if name.startswith("flightcheck"):
                assert name.startswith(
                    ("flightcheck.core", "flightcheck.mock", "flightcheck.runtime")
                ), (path.name, name)
            assert name.split(".")[0] not in {"openai", "importlib", "runpy"}, (path.name, name)


def test_subprocess_is_imported_only_by_the_harness() -> None:
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if "subprocess" in {n.split(".")[0] for n in _imports(path)}
    ]
    assert offenders == ["runtime/harness.py"]


def test_harness_never_uses_a_shell() -> None:
    tree = ast.parse((RUNTIME_DIR / "harness.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "shell":
            assert isinstance(node.value, ast.Constant) and node.value.value is False
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"system", "popen", "check_output", "getoutput"}
