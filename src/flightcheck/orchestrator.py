"""Use cases: one function per command, request in, ``Report`` out. No output formatting here."""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from flightcheck import __version__
from flightcheck.core.config import FlightCheckConfig, TestConfig
from flightcheck.core.errors import ConfigError
from flightcheck.core.logging import get_logger
from flightcheck.core.models import (
    AppRun,
    CheckResult,
    CheckStatus,
    Diagnostic,
    Location,
    RecordedExchange,
    RepoMetadata,
    Report,
    ReportMetadata,
    ScanRequest,
    ScenarioResult,
    Summary,
    TestRequest,
    worst_status,
)
from flightcheck.mock.server import MockServer
from flightcheck.runtime.harness import (
    AppLaunchError,
    build_child_env,
    ensure_consent,
    make_fake_key,
    resolve_cwd,
    run_app,
)
from flightcheck.runtime.scenarios import (
    KNOWN_SCENARIO_IDS,
    P1_SCENARIOS,
    PHASE_7B_SCENARIOS,
    SCENARIOS,
    Scenario,
)
from flightcheck.runtime.validators import (
    CheckConfig,
    by_seq,
    error_all,
    evaluate,
    report_once,
    skip_all,
    to_finding,
)
from flightcheck.scanner.detect import detect
from flightcheck.scanner.discovery import DiscoveryOptions
from flightcheck.scanner.engine import BharatCodeMode, RuleContext, run_rules
from flightcheck.scanner.parser import RepoIndex, build_index

PROVIDER_OVERRIDES = ("bharatcode", "openai_compatible")


def _provider_mode(
    request: ScanRequest, config: FlightCheckConfig
) -> tuple[BharatCodeMode, str | None]:
    """BharatCode rule mode and the ``declared_provider`` passed to detection."""
    if request.provider_override is not None and request.provider_override not in (
        PROVIDER_OVERRIDES
    ):
        raise ConfigError(
            f"Unknown provider: {request.provider_override}",
            hint="Use --provider auto, bharatcode or openai_compatible.",
        )
    name = request.provider_override or config.provider
    if name is None:
        return "auto", None
    profile = config.providers.get(name)
    kind = profile.kind if profile is not None else name
    if kind == "bharatcode":
        return ("forced" if request.provider_override else "auto"), "bharatcode"
    if kind == "openai_compatible":
        return "disabled", None
    return "auto", None


def run_scan(request: ScanRequest, config: FlightCheckConfig) -> Report:
    """Discover → parse → index → detect → rules → ``Report``. Never executes repository code."""
    root = Path(request.root)
    if not root.exists():
        raise ConfigError(f"Path not found: {root} — pass a directory containing your app")
    mode, declared = _provider_mode(request, config)
    index = build_index(root, DiscoveryOptions.from_config(config.scan))
    detection = detect(index, declared_provider=declared)
    ctx = RuleContext(index=index, detection=detection, bharatcode_mode=mode)
    findings = run_rules(index, ctx, rule_ids=request.rule_ids, min_severity=request.min_severity)
    resolved = root.resolve()
    metadata = ReportMetadata(
        tool_version=__version__,
        generated_at=datetime.now(UTC),
        command="scan",
        options={
            "provider": request.provider_override or "auto",
            "rules": sorted(request.rule_ids) if request.rule_ids else None,
            "min_severity": request.min_severity.value,
        },
        python_version=platform.python_version(),
        platform=platform.system().lower() or "unknown",
        mock_fidelity="n/a",
    )
    repo = RepoMetadata(
        root_name=resolved.name,
        python_files_scanned=len(index.files),
        files_skipped=dict(index.skipped),
        total_bytes_scanned=index.total_bytes,
    )
    return Report(
        metadata=metadata,
        repo=repo,
        providers_detected=detection.as_list(),
        findings=findings,
        diagnostics=[*index.diagnostics, *detection.diagnostics, *ctx.diagnostics],
        summary=Summary.from_report_parts(findings),
    )


# --- test (Phase 7A; the CLI command arrives in Phase 7B) -----------------------------------

_log = get_logger("orchestrator")
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def _is_non_local_url(text: str) -> bool:
    candidate = text.strip()
    if not candidate.lower().startswith(("http://", "https://")):
        return False
    try:
        host = urlsplit(candidate).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.lower()
    local = host in _LOCAL_HOSTS or host.endswith(".localhost") or host.startswith("127.")
    return not local


def hardcoded_base_urls(index: RepoIndex) -> list[Location]:
    """Pre-flight (plan §17.2 step 3): where a non-local base URL is a literal in the source.

    A client whose ``base_url`` is read from an environment variable can be redirected (even
    with a literal default); a literal (or one-hop constant) cannot, and neither can a raw HTTP
    call to a literal absolute URL. Uses the scanner's index only; nothing is executed.
    """
    found: dict[tuple[str, int], Location] = {}
    for binding in index.clients:
        if "base_url" not in binding.kwargs or "base_url" in binding.env_kwargs:
            continue
        value = binding.kwargs["base_url"]
        text = value if isinstance(value, str) else "".join(binding.base_url_fragments)
        if _is_non_local_url(text):
            found.setdefault((binding.location.path, binding.location.line), binding.location)
    for call in index.call_sites:
        if call.url and _is_non_local_url(call.url):
            found.setdefault((call.location.path, call.location.line), call.location)
    return sorted(found.values(), key=lambda loc: (loc.path, loc.line))


def _preflight_reason(locations: Sequence[Location]) -> str:
    where = ", ".join(f"{loc.path}:{loc.line}" for loc in locations[:5])
    if len(locations) > 5:
        where += f" and {len(locations) - 5} more"
    return (
        f"base URL is hard-coded at {where} — FlightCheck cannot redirect traffic without "
        "changing your code; read it from an environment variable"
    )


def _select_scenarios(ids: Sequence[str]) -> tuple[list[Scenario], list[Diagnostic]]:
    unknown = [sid for sid in ids if sid not in KNOWN_SCENARIO_IDS]
    if unknown:
        raise ConfigError(
            f"Unknown scenario ID(s): {', '.join(sorted(set(unknown)))}",
            hint=f"Use scenario IDs from: {', '.join(KNOWN_SCENARIO_IDS)}.",
        )
    selected: list[Scenario] = []
    notes: list[Diagnostic] = []
    for sid in dict.fromkeys(ids):
        if sid in SCENARIOS:
            selected.append(SCENARIOS[sid])
        elif sid in PHASE_7B_SCENARIOS:
            notes.append(
                Diagnostic(
                    level="info",
                    code="scenario_not_implemented",
                    message=f"Scenario {sid} ({PHASE_7B_SCENARIOS[sid]}) arrives in Phase 7B; "
                    "it was not run.",
                )
            )
        else:
            notes.append(
                Diagnostic(
                    level="info",
                    code="scenario_not_implemented",
                    message=f"Scenario {sid} ({P1_SCENARIOS[sid]}) is planned (P1) and not "
                    "implemented yet; it was not run.",
                )
            )
    return selected, notes


def _path_aliases(root: Path) -> dict[str, str]:
    """Absolute paths shown in child output → short names (no user directory layout in
    reports, like ``RepoMetadata.root_name``)."""
    aliases = {str(root.resolve()): f"<{root.resolve().name}>"}
    try:
        aliases.setdefault(str(Path.home()), "~")
    except RuntimeError:
        pass
    return aliases


def _run_scenario(
    scenario: Scenario,
    test: TestConfig,
    cwd: Path,
    parent_env: Mapping[str, str],
    path_aliases: Mapping[str, str],
) -> tuple[AppRun | None, list[RecordedExchange], list[CheckResult]]:
    """Fresh mock + fresh child for one scenario. ``MockServerError`` propagates (exit 3)."""
    fake_key = make_fake_key()
    server = MockServer(
        scenario.script_factory(), fake_key=fake_key, max_requests=test.max_requests
    )
    recorder = server.recorder
    fired: list[bool] = []

    def over_limit() -> bool:
        if len(recorder) > test.max_requests:
            fired.append(True)
            return True
        return False

    app_run: AppRun | None = None
    launch_error: AppLaunchError | None = None
    server.start()
    try:
        env = build_child_env(parent_env, test, server.base_url, fake_key)
        try:
            app_run = run_app(
                test.command,
                cwd,
                env,
                test.stdin,
                test.timeout_s,
                abort_when=over_limit,
                path_aliases=path_aliases,
            )
        except AppLaunchError as exc:
            launch_error = exc
    finally:
        server.stop()  # waits for in-flight requests: read the recorder only after this
    exchanges = by_seq(recorder.list())
    if launch_error is not None:
        return (
            None,
            exchanges,
            error_all(scenario, f"{launch_error.message} Hint: {launch_error.hint}."),
        )
    config = CheckConfig(
        test=test,
        recording_complete=recorder.complete,
        missing_records=recorder.dropped + recorder.record_failures,
        aborted_for_limit=bool(fired),
    )
    assert app_run is not None
    _log.info(
        "scenario %s: exit=%s timed_out=%s requests=%d",
        scenario.id,
        app_run.exit_code,
        app_run.timed_out,
        len(exchanges),
    )
    return app_run, exchanges, evaluate(scenario, exchanges, app_run, config)


def run_test(
    request: TestRequest,
    config: FlightCheckConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> Report:
    """Consent → pre-flight → for each selected scenario: fresh mock + fresh child → checks.

    ``environ`` is the parent environment the child's scrubbed environment is built from
    (default ``os.environ``). Runs nothing without ``request.allow_exec`` and nothing when the
    pre-flight finds a hard-coded non-local base URL.
    """
    root = Path(request.root)
    ensure_consent(request.allow_exec, target=str(root))
    if not root.is_dir():
        raise ConfigError(f"Path not found: {root} — pass the directory containing your app")
    test = config.test
    if test is None:
        raise ConfigError(
            "No [test] section in flightcheck.toml: FlightCheck does not know how to run the app.",
            hint='Add [test] with command = ["python", "app.py", "<question>"].',
        )
    selected, notes = _select_scenarios(
        request.scenarios if request.scenarios is not None else test.scenarios
    )
    cwd = resolve_cwd(root, test.cwd)
    if not cwd.is_dir():
        raise ConfigError(f"[test].cwd is not a directory: {test.cwd}")

    mode, declared = _provider_mode(ScanRequest(root=root), config)
    index = build_index(root, DiscoveryOptions.from_config(config.scan))
    detection = detect(index, declared_provider=declared)
    static_findings = []
    scan_diagnostics: list[Diagnostic] = []
    if request.include_scan:
        ctx = RuleContext(index=index, detection=detection, bharatcode_mode=mode)
        static_findings = run_rules(index, ctx)
        scan_diagnostics = ctx.diagnostics

    hardcoded = hardcoded_base_urls(index)
    if hardcoded:
        reason = _preflight_reason(hardcoded)
        notes.append(
            Diagnostic(
                level="warning",
                code="test_preflight_refused",
                message=reason,
                path=hardcoded[0].path,
            )
        )
    parent_env = os.environ if environ is None else environ
    aliases = _path_aliases(root)
    runs: list[tuple[Scenario, AppRun | None, list[RecordedExchange], list[CheckResult]]] = []
    for scenario in selected:
        if hardcoded:
            runs.append((scenario, None, [], skip_all(scenario, reason)))
            continue
        runs.append((scenario, *_run_scenario(scenario, test, cwd, parent_env, aliases)))

    checks_once = report_once([(exchanges, checks) for _, _, exchanges, checks in runs])
    scenarios: list[ScenarioResult] = []
    for (scenario, app_run, exchanges, _), checks in zip(runs, checks_once, strict=True):
        status = worst_status(check.status for check in checks)
        assert status is not None  # every scenario has its own checks
        scenarios.append(
            ScenarioResult(
                scenario_id=scenario.id,
                name=scenario.name,
                status=status,
                app_run=app_run,
                exchanges=exchanges,
                checks=checks,
            )
        )
    runtime_findings = [
        to_finding(check)
        for result in scenarios
        for check in result.checks
        if check.status is CheckStatus.FAILED
    ]
    findings = [*static_findings, *runtime_findings]
    resolved = root.resolve()
    metadata = ReportMetadata(
        tool_version=__version__,
        generated_at=datetime.now(UTC),
        command="test",
        options={
            "scenarios": [s.id for s in selected],
            "include_scan": request.include_scan,
            "timeout_s": test.timeout_s,
            "max_requests": test.max_requests,
        },
        python_version=platform.python_version(),
        platform=platform.system().lower() or "unknown",
        mock_fidelity="documentation_model",
    )
    repo = RepoMetadata(
        root_name=resolved.name,
        python_files_scanned=len(index.files),
        files_skipped=dict(index.skipped),
        total_bytes_scanned=index.total_bytes,
    )
    return Report(
        metadata=metadata,
        repo=repo,
        providers_detected=detection.as_list(),
        findings=findings,
        scenarios=scenarios,
        diagnostics=[*index.diagnostics, *detection.diagnostics, *scan_diagnostics, *notes],
        summary=Summary.from_report_parts(findings, scenarios),
    )


__all__ = ["hardcoded_base_urls", "run_scan", "run_test"]
