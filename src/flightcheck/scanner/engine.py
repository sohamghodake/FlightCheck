"""Static rule engine (plan §16.1 step 5): a dict registry, a decorator, and ``run_rules``.

Rules are plain functions over the :class:`RepoIndex`. They never read anything but the index
and (through :meth:`RuleContext.excerpt`) the already-discovered source files. Execution order
is the registry order; findings are sorted by ``(path, line, rule_id)``. One crashing rule
produces an ``error`` diagnostic (``rule_crashed``) and never aborts the scan.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from flightcheck.core.errors import ConfigError
from flightcheck.core.models import (
    Confidence,
    Diagnostic,
    Evidence,
    Finding,
    FindingKind,
    Location,
    ProviderDetection,
    Severity,
    Verification,
)
from flightcheck.core.redact import redact
from flightcheck.scanner.ast_utils import (
    SEGMENT_MAX_LEN,
    is_placeholder_key,
    prefixed_secrets,
)
from flightcheck.scanner.detect import DetectionResult
from flightcheck.scanner.parser import CallSite, RepoIndex, decode_source

BharatCodeMode = Literal["auto", "forced", "disabled"]
ALL_PROVIDERS = "*"


@dataclass(frozen=True)
class RuleMeta:
    id: str  # "FC-BC-001"; permanent, never reused (ADR-012)
    title: str
    category: Literal["bharatcode-contract", "robustness", "security"]
    default_severity: Severity
    default_confidence: Confidence
    providers: frozenset[str]  # {"bharatcode"} or {"*"}
    doc_source: str | None  # URL backing the rule
    remediation: str  # deterministic fix text (used in no-LLM mode)
    priority: Literal["P0", "P1", "P2"]
    kind: FindingKind = FindingKind.STATIC


@dataclass
class RuleContext:
    """What a rule may consult besides the index: provider detection and source excerpts."""

    index: RepoIndex
    detection: DetectionResult
    # auto: BharatCode rules run on call sites classified ``bharatcode`` (any confidence);
    # forced: ``--provider bharatcode``; disabled: ``--provider openai_compatible``.
    bharatcode_mode: BharatCodeMode = "auto"
    diagnostics: list[Diagnostic] = field(default_factory=list)
    _sources: dict[str, list[str]] = field(default_factory=dict, repr=False)
    # Hard-coded key literals found before rules run (ADR-026): raw value -> replacement.
    # ``mask`` applies them to every excerpt and message, then ``core.redact``.
    secrets: dict[str, str] = field(default_factory=dict, repr=False)

    def collect_secrets(self) -> None:
        """Record every literal FC-SEC-001 treats as a key, so no rule can echo it."""
        for file in self.index.files:
            for literal in file.string_literals:
                for value, replacement in prefixed_secrets(literal.value):
                    self.secrets.setdefault(value, replacement)
            for keyed in file.keyed_literals:
                if not is_placeholder_key(keyed.value):
                    self.secrets.setdefault(keyed.value, "[REDACTED]")

    def mask(self, text: str) -> str:
        """``text`` with collected key literals replaced, then passed through ``redact``.

        Values shorter than 4 characters are replaced only in quoted form (``"abc"``), so a
        short key cannot mangle unrelated text.
        """
        for value in sorted(self.secrets, key=len, reverse=True):
            replacement = self.secrets[value]
            if len(value) >= 4:
                text = text.replace(value, replacement)
            else:
                for quote in ('"', "'"):
                    text = text.replace(f"{quote}{value}{quote}", f"{quote}{replacement}{quote}")
        return redact(text)

    def provider_of(self, call: CallSite) -> ProviderDetection | None:
        return self.detection.call_sites.get(call.id)

    def applies(self, meta: RuleMeta, call: CallSite) -> bool:
        if ALL_PROVIDERS in meta.providers:
            return True
        if "bharatcode" in meta.providers:
            if self.bharatcode_mode == "disabled":
                return False
            if self.bharatcode_mode == "forced":
                return True
            detection = self.provider_of(call)
            return detection is not None and detection.provider == "bharatcode"
        detection = self.provider_of(call)
        return detection is not None and detection.provider in meta.providers

    def call_sites_for(self, meta: RuleMeta) -> list[CallSite]:
        return [call for call in self.index.call_sites if self.applies(meta, call)]

    def _lines(self, path: str) -> list[str]:
        if path not in self._sources:
            try:
                text, _ = decode_source((self.index.root / path).read_bytes())
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
                text = ""
            self._sources[path] = text.splitlines()
        return self._sources[path]

    def excerpt(self, location: Location) -> str:
        """Redacted source lines of ``location`` (dedented, at most 300 characters)."""
        lines = self._lines(location.path)
        if location.line < 1 or location.line > len(lines):
            return ""
        end = max(location.end_line or location.line, location.line)
        text = textwrap.dedent("\n".join(lines[location.line - 1 : end])).strip("\n")
        if len(text) > SEGMENT_MAX_LEN:
            text = text[: SEGMENT_MAX_LEN - 1] + "…"
        return self.mask(text)

    def finding(
        self,
        meta: RuleMeta,
        call: CallSite,
        message: str,
        *,
        severity: Severity | None = None,
        confidence: Confidence | None = None,
    ) -> Finding:
        """A finding at ``call`` with source and documentation evidence."""
        return self.finding_at(
            meta, call.location, message, severity=severity, confidence=confidence, call=call
        )

    def finding_at(
        self,
        meta: RuleMeta,
        location: Location,
        message: str,
        *,
        severity: Severity | None = None,
        confidence: Confidence | None = None,
        call: CallSite | None = None,
    ) -> Finding:
        """A finding at any ``location`` (dict literal, print call, client constructor, …).

        ``call`` (optional) names the related call site for the finding's ``provider``.
        """
        loc = location
        evidence = [Evidence(type="source", location=loc, excerpt=self.excerpt(loc))]
        if meta.doc_source:
            evidence.append(Evidence(type="documentation", note=f"Documented at {meta.doc_source}"))
        detection = self.provider_of(call) if call is not None else None
        return Finding(
            id=Finding.make_id(meta.id, loc.path, loc.line, loc.column),
            rule_id=meta.id,
            title=meta.title,
            kind=meta.kind,
            verification=Verification.STATIC,
            severity=severity or meta.default_severity,
            confidence=confidence or meta.default_confidence,
            message=self.mask(message),
            location=loc,
            evidence=evidence,
            remediation=meta.remediation,
            doc_url=meta.doc_source,
            provider=detection.provider if detection else None,
        )


RuleFn = Callable[[RepoIndex, RuleContext], Iterable[Finding]]
Registry = dict[str, tuple[RuleMeta, RuleFn]]

_REGISTRY: Registry = {}


def rule(meta: RuleMeta, registry: Registry | None = None) -> Callable[[RuleFn], RuleFn]:
    """Register ``fn`` under ``meta.id``. A duplicate ID is a programming error."""
    target = _REGISTRY if registry is None else registry

    def deco(fn: RuleFn) -> RuleFn:
        if meta.id in target:
            raise RuntimeError(f"duplicate rule id {meta.id}")
        target[meta.id] = (meta, fn)
        return fn

    return deco


def load_builtin_rules() -> Registry:
    """Import the built-in rule modules (their decorators fill the registry)."""
    from flightcheck.scanner import rules as _rules  # noqa: F401

    return _REGISTRY


def select_rules(rule_ids: Iterable[str] | None, registry: Registry) -> list[str]:
    """Rule IDs to run, in registry order. Unknown IDs → :class:`ConfigError` (exit 2)."""
    if rule_ids is None:
        return list(registry)
    wanted = {rule_id.strip().upper() for rule_id in rule_ids}
    unknown = sorted(wanted - set(registry))
    if unknown:
        raise ConfigError(
            f"Unknown rule ID: {', '.join(unknown)}. Valid IDs: {', '.join(sorted(registry))}",
            hint="Pass --rule with one of the valid IDs, or omit it to run every rule.",
        )
    return [rule_id for rule_id in registry if rule_id in wanted]


_DOWNGRADE = {
    Confidence.HIGH: Confidence.MEDIUM,
    Confidence.MEDIUM: Confidence.LOW,
    Confidence.LOW: Confidence.LOW,
}


def _call_id(location: Location | None) -> str | None:
    if location is None:
        return None
    return f"{location.path}:{location.line}:{location.column}"


def _downgrade_for_provider(finding: Finding, ctx: RuleContext, meta: RuleMeta) -> Finding:
    """Lower confidence one level when the call site's provider confidence is ``low``.

    Only provider-specific rules are downgraded: an unsure provider says nothing about a
    provider-independent (``"*"``) rule such as FC-GEN-004 (ADR-026).
    """
    if ctx.bharatcode_mode == "forced" or ALL_PROVIDERS in meta.providers:
        return finding
    call_id = _call_id(finding.location)
    detection = ctx.detection.call_sites.get(call_id) if call_id else None
    if detection is None or detection.confidence is not Confidence.LOW:
        return finding
    return finding.model_copy(update={"confidence": _DOWNGRADE[finding.confidence]})


def sort_key(finding: Finding) -> tuple[str, int, str, int, str]:
    loc = finding.location
    path = loc.path if loc else ""
    return (path, loc.line if loc else 0, finding.rule_id, loc.column if loc else 0, finding.id)


def run_rules(
    index: RepoIndex,
    ctx: RuleContext,
    *,
    rule_ids: Iterable[str] | None = None,
    min_severity: Severity = Severity.INFO,
    registry: Registry | None = None,
) -> list[Finding]:
    """Run enabled rules; one crashing rule produces an ``error`` diagnostic, never aborts."""
    reg = load_builtin_rules() if registry is None else registry
    ctx.collect_secrets()
    findings: list[Finding] = []
    for rule_id in select_rules(rule_ids, reg):
        meta, fn = reg[rule_id]
        if meta.providers == frozenset({"bharatcode"}) and ctx.bharatcode_mode == "disabled":
            continue
        try:
            produced = list(fn(index, ctx))
        except Exception as exc:  # crash isolation: report and continue
            ctx.diagnostics.append(
                Diagnostic(
                    level="error",
                    code="rule_crashed",
                    message=ctx.mask(f"Rule {rule_id} crashed: {type(exc).__name__}: {exc}"),
                )
            )
            continue
        findings.extend(_downgrade_for_provider(f, ctx, meta) for f in produced)
    kept = [f for f in findings if f.severity.rank >= min_severity.rank]
    return sorted(kept, key=sort_key)


__all__ = [
    "ALL_PROVIDERS",
    "RuleContext",
    "RuleFn",
    "RuleMeta",
    "load_builtin_rules",
    "rule",
    "run_rules",
    "select_rules",
    "sort_key",
]
