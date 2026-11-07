"""Use cases: one function per command, request in, ``Report`` out. No output formatting here."""

from __future__ import annotations

import platform
from datetime import UTC, datetime
from pathlib import Path

from flightcheck import __version__
from flightcheck.core.config import FlightCheckConfig
from flightcheck.core.errors import ConfigError
from flightcheck.core.models import RepoMetadata, Report, ReportMetadata, ScanRequest, Summary
from flightcheck.scanner.detect import detect
from flightcheck.scanner.discovery import DiscoveryOptions
from flightcheck.scanner.engine import BharatCodeMode, RuleContext, run_rules
from flightcheck.scanner.parser import build_index

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


__all__ = ["run_scan"]
