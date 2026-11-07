"""Consent-gated child-process harness (plan §17.2, §20.6–20.8; ADR-005, ADR-017).

This is the only module in FlightCheck that runs repository code, and it does so only as a
separate process started from the configured ``[test].command`` list: never ``shell=True``,
never an in-process import. Callers must call :func:`ensure_consent` first.

The child gets a scrubbed environment (:func:`build_child_env`): an allowlist of variables, the
mock URL in every ``base_url_env`` variable, a fake key in every ``api_key_env`` variable, and an
egress guard (``HTTP_PROXY``/``HTTPS_PROXY`` → an unused local port, ``NO_PROXY`` for
127.0.0.1/localhost). The guard is best effort, **not a sandbox**: it only stops HTTP clients
that honour the proxy variables (httpx2 and the OpenAI SDK do, with their default
``trust_env=True``: httpx2/_client.py:658 and httpx2/_utils.py:31-80 in the installed 2.16.0).
A child can still open raw sockets, read files, or ignore the variables.
"""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

from flightcheck.core.config import TestConfig
from flightcheck.core.errors import ConsentRequiredError, FlightCheckError
from flightcheck.core.models import AppRun
from flightcheck.core.paths import resolve_inside
from flightcheck.core.redact import redact

FAKE_KEY_PREFIX = "fc_fake_key_"
OUTPUT_LIMIT_BYTES = 8 * 1024  # per stream, after redaction (§20.7)
CAPTURE_LIMIT_BYTES = 1024 * 1024  # raw bytes kept per stream; the rest is drained and dropped
TERMINATE_GRACE_S = 3.0  # terminate → wait this long → kill the whole group
READER_JOIN_S = 2.0
POLL_S = 0.05
TRUNCATION_MARK = "\n…[output truncated by FlightCheck]"

# §20.7 allowlist. PYTHONPATH is copied only when set; PYTHONIOENCODING is always utf-8.
ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "VIRTUAL_ENV",
    "PYTHONPATH",
)
LOCAL_NO_PROXY = "127.0.0.1,localhost"
_PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
_IS_WINDOWS = os.name == "nt"


class AppLaunchError(FlightCheckError):
    """The configured command could not be started (not found, not executable, bad cwd)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, hint="check [test].command and [test].cwd in flightcheck.toml")


def make_fake_key() -> str:
    """A fresh fake API key: ``fc_fake_key_<random>``. Never a real credential."""
    return FAKE_KEY_PREFIX + secrets.token_hex(12)


def ensure_consent(allow_exec: bool, target: str = ".") -> None:
    """Raise :class:`ConsentRequiredError` unless the user passed ``--allow-exec``."""
    if allow_exec is not True:
        raise ConsentRequiredError(
            "flightcheck test runs your app's [test].command as a child process; "
            "it needs your explicit consent.",
            hint=f"flightcheck test {target} --allow-exec",
        )


def unused_local_port() -> int:
    """A 127.0.0.1 port nothing listens on (bound, read, released), for the egress guard."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_child_env(
    parent_env: Mapping[str, str],
    test_config: TestConfig,
    mock_url: str,
    fake_key: str,
    *,
    guard_port: int | None = None,
) -> dict[str, str]:
    """The child's whole environment (§20.7): allowlist → overrides → egress guard.

    Everything not on the allowlist is dropped (API keys, cloud credentials, tokens, inherited
    proxies in any case). The guard is applied last, so a ``base_url_env``/``api_key_env`` entry
    cannot switch it off.
    """
    env: dict[str, str] = {}
    for name in ENV_ALLOWLIST:
        value = parent_env.get(name)
        if value:
            env[name] = value
    env["PYTHONIOENCODING"] = "utf-8"
    for name in test_config.base_url_env:
        env[name] = mock_url
    for name in test_config.api_key_env:
        env[name] = fake_key
    for name in list(env):
        if name.upper() in _PROXY_NAMES:
            del env[name]
    port = unused_local_port() if guard_port is None else guard_port
    dead_proxy = f"http://127.0.0.1:{port}"
    env["HTTP_PROXY"] = dead_proxy
    env["HTTPS_PROXY"] = dead_proxy
    env["NO_PROXY"] = LOCAL_NO_PROXY
    return env


def _excerpt(raw: bytes, path_aliases: Mapping[str, str] | None = None) -> str:
    """Decode (invalid UTF-8 replaced), shorten known absolute paths, redact, then truncate
    to 8 KB of UTF-8."""
    text = raw.decode("utf-8", errors="replace")
    for path in sorted(path_aliases or {}, key=len, reverse=True):
        if len(path) > 1:
            text = text.replace(path, (path_aliases or {})[path])
    text = redact(text)
    encoded = text.encode("utf-8")
    if len(encoded) <= OUTPUT_LIMIT_BYTES:
        return text
    keep = OUTPUT_LIMIT_BYTES - len(TRUNCATION_MARK.encode("utf-8"))
    return encoded[:keep].decode("utf-8", errors="ignore") + TRUNCATION_MARK


def _read_capped(stream: IO[bytes], sink: bytearray) -> None:
    """Read until EOF, keeping the first CAPTURE_LIMIT_BYTES (a chatty app cannot fill memory)."""
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            room = CAPTURE_LIMIT_BYTES - len(sink)
            if room > 0:
                sink.extend(chunk[:room])
    except (OSError, ValueError):
        return


def _write_stdin(stream: IO[bytes], data: bytes) -> None:
    try:
        if data:
            stream.write(data)
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _signal_group(proc: subprocess.Popen[bytes], *, kill: bool) -> None:
    """Terminate (or kill) the child and everything in its process group."""
    if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
        if kill:
            # taskkill /T also ends grandchildren; best effort.
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    shell=False,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                proc.kill()
            except OSError:
                pass
        else:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            except OSError:
                pass
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL if kill else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def run_app(
    command: Sequence[str],
    cwd: str | os.PathLike[str],
    env: Mapping[str, str],
    stdin: str,
    timeout_s: float,
    *,
    abort_when: Callable[[], bool] | None = None,
    path_aliases: Mapping[str, str] | None = None,
) -> AppRun:
    """Run ``command`` (a list, ``shell=False``) and return a redacted :class:`AppRun`.

    ``stdin`` is written and then closed (empty → immediate EOF; the parent's stdin is never
    inherited). The child starts in a new session/process group; on timeout (or when
    ``abort_when()`` becomes true) the group is terminated, given 3 s, then killed. After the
    child exits, anything left in its group is killed too, so grandchildren do not outlive the
    run. Output is decoded with ``errors="replace"``, redacted, then truncated to 8 KB per stream.
    ``path_aliases`` maps absolute paths (the project root, the home directory) to short names,
    so reports do not carry the user's directory layout.
    """
    argv = list(command)
    if not argv or not all(isinstance(part, str) and part for part in argv):
        raise AppLaunchError("[test].command must be a non-empty list of non-empty strings.")
    popen_kwargs: dict[str, object] = {}
    if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # list argv, shell=False, consented
            argv,
            shell=False,
            cwd=os.fspath(cwd),
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            **popen_kwargs,  # type: ignore[arg-type]
        )
    except FileNotFoundError as exc:
        missing = exc.filename if exc.filename else argv[0]
        raise AppLaunchError(
            f"Cannot start the app: {redact(str(missing))!r} was not found "
            "(is it on PATH, and does [test].cwd exist?)."
        ) from None
    except (PermissionError, NotADirectoryError, OSError) as exc:
        raise AppLaunchError(
            f"Cannot start the app: {type(exc).__name__} for {redact(argv[0])!r}."
        ) from None

    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    out_buf, err_buf = bytearray(), bytearray()
    threads = [
        threading.Thread(target=_read_capped, args=(proc.stdout, out_buf), daemon=True),
        threading.Thread(target=_read_capped, args=(proc.stderr, err_buf), daemon=True),
        threading.Thread(
            target=_write_stdin, args=(proc.stdin, stdin.encode("utf-8")), daemon=True
        ),
    ]
    for thread in threads:
        thread.start()

    deadline = started + timeout_s
    timed_out = False
    try:
        while True:
            try:
                proc.wait(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() >= deadline:
                timed_out = True
            elif abort_when is not None and abort_when():
                pass
            else:
                continue
            _signal_group(proc, kill=False)
            try:
                proc.wait(timeout=TERMINATE_GRACE_S)
            except subprocess.TimeoutExpired:
                _signal_group(proc, kill=True)
                try:
                    proc.wait(timeout=TERMINATE_GRACE_S)
                except subprocess.TimeoutExpired:
                    pass  # reported with exit_code None
            break
    finally:
        # Grandchildren left in the group (normal exit, timeout or an exception here). The
        # group id is the child's pid; it stays valid while any member lives.
        _signal_group(proc, kill=True)
        if proc.returncode is None:
            try:
                proc.wait(timeout=TERMINATE_GRACE_S)
            except subprocess.TimeoutExpired:
                pass
        for thread in threads:
            thread.join(READER_JOIN_S)
        # A reader still blocked (a grandchild that left the group holds the pipe) keeps its
        # pipe: closing a buffered reader another thread is reading can deadlock.
        for thread, pipe in zip(threads[:2], (proc.stdout, proc.stderr), strict=True):
            if not thread.is_alive():
                try:
                    pipe.close()
                except OSError:
                    pass

    duration_ms = int((time.monotonic() - started) * 1000)
    return AppRun(
        command=[redact(part) for part in argv],
        exit_code=proc.returncode,
        timed_out=timed_out,
        duration_ms=duration_ms,
        stdout_excerpt=_excerpt(bytes(out_buf), path_aliases),
        stderr_excerpt=_excerpt(bytes(err_buf), path_aliases),
    )


def resolve_cwd(root: str | os.PathLike[str], cwd: str) -> Path:
    """``[test].cwd`` resolved inside the project root (PathOutsideRootError otherwise)."""
    return resolve_inside(root, cwd)


__all__ = [
    "FAKE_KEY_PREFIX",
    "OUTPUT_LIMIT_BYTES",
    "AppLaunchError",
    "build_child_env",
    "ensure_consent",
    "make_fake_key",
    "resolve_cwd",
    "run_app",
    "unused_local_port",
]
