"""Security rules FC-SEC-001…002 (plan §16.3).

Raw key literals never reach a report: the engine collects every literal FC-SEC-001 treats as
a key before any rule runs and masks it in all excerpts and messages (ADR-026). Messages here
never include the literal itself.
"""

from __future__ import annotations

from collections.abc import Iterator

from flightcheck.core.models import Confidence, Finding, Severity
from flightcheck.scanner.ast_utils import is_placeholder_key, prefixed_secrets
from flightcheck.scanner.engine import ALL_PROVIDERS, RuleContext, RuleMeta, rule
from flightcheck.scanner.parser import RepoIndex

ANY = frozenset({ALL_PROVIDERS})


def _meta(
    rule_id: str, title: str, severity: Severity, confidence: Confidence, fix: str
) -> RuleMeta:
    return RuleMeta(
        id=rule_id,
        title=title,
        category="security",
        default_severity=severity,
        default_confidence=confidence,
        providers=ANY,
        doc_source=None,
        remediation=fix,
        priority="P0",
    )


# --- FC-SEC-001 -----------------------------------------------------------------------------

SEC001 = _meta(
    "FC-SEC-001",
    "Hard-coded API key",
    Severity.CRITICAL,
    Confidence.HIGH,
    "Read the key from the BHARATCODE_API_KEY environment variable "
    '(os.environ["BHARATCODE_API_KEY"]) and rotate the exposed key.',
)
_PLACEHOLDER_NOTE = (
    "looks like a placeholder, not a real key; make sure the real key comes from the "
    "environment (BHARATCODE_API_KEY)."
)


@rule(SEC001)
def hard_coded_key(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for file in index.files:
        reported: set[tuple[int, int]] = set()
        for literal in file.string_literals:
            matches = prefixed_secrets(literal.value)
            if not matches:
                continue
            loc = literal.location
            reported.add((loc.line, loc.column))
            masked = matches[0][1]  # e.g. "bc_live_…[REDACTED]"; the raw value never appears
            prefix = masked.split("…", 1)[0]
            if is_placeholder_key(literal.value):
                yield ctx.finding_at(
                    SEC001,
                    loc,
                    f"String literal with the `{prefix}` key prefix {_PLACEHOLDER_NOTE}",
                    severity=Severity.INFO,
                )
                continue
            yield ctx.finding_at(
                SEC001,
                loc,
                f"Hard-coded API key `{masked}`: a string literal with the `{prefix}` key "
                "prefix is in source code. Anyone with the code has the key.",
            )
        for keyed in file.keyed_literals:
            loc = keyed.location
            if (loc.line, loc.column) in reported:
                continue
            reported.add((loc.line, loc.column))
            if is_placeholder_key(keyed.value):
                yield ctx.finding_at(
                    SEC001,
                    loc,
                    f"Literal given to `{keyed.key}` {_PLACEHOLDER_NOTE}",
                    severity=Severity.INFO,
                    confidence=Confidence.MEDIUM,
                )
                continue
            yield ctx.finding_at(
                SEC001,
                loc,
                f"Hard-coded API key: a string literal is given to `{keyed.key}` "
                "(value redacted). Keys in source code leak with the code.",
                severity=Severity.HIGH,
                confidence=Confidence.MEDIUM,
            )


# --- FC-SEC-002 -----------------------------------------------------------------------------

SEC002 = _meta(
    "FC-SEC-002",
    "Credential written to logs or stdout",
    Severity.HIGH,
    Confidence.MEDIUM,
    "Never print or log credentials (not even a prefix); log the response's request_id instead.",
)


@rule(SEC002)
def credential_logged(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for file in index.files:
        for call in file.log_calls:
            names = ", ".join(f"`{ref}`" for ref in call.refs)
            yield ctx.finding_at(
                SEC002,
                call.location,
                f"`{call.callee}` references {names}: a credential may be written to logs "
                "or stdout.",
            )
