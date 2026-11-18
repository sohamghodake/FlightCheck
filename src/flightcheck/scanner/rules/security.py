"""Security rules FC-SEC-001…002 (plan §16.3).

Raw key literals never reach a report: the engine collects every literal FC-SEC-001 treats as
a key before any rule runs and masks it in all excerpts and messages (ADR-026). Messages here
never include the literal itself.

Phase G3 (ADR-037): FC-SEC-001 also knows Groq's key prefix and placeholders (contract A4). Its
remediation names ``GROQ_API_KEY`` (contract A3) when the key is Groq's (prefix, a name with the
word GROQ, or a client classified ``groq``), keeps the BharatCode wording for BharatCode keys,
and is provider-neutral otherwise.
"""

from __future__ import annotations

from collections.abc import Iterator

from flightcheck.core.config import GROQ_API_KEY_ENV
from flightcheck.core.models import Confidence, Finding, Location, Severity
from flightcheck.scanner.ast_utils import is_placeholder_key, prefixed_secrets
from flightcheck.scanner.detect import is_groq_env_name
from flightcheck.scanner.engine import ALL_PROVIDERS, RuleContext, RuleMeta, rule
from flightcheck.scanner.parser import FileIndex, RepoIndex

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
    "Read the key from your provider's API-key environment variable (e.g. "
    'os.environ["GROQ_API_KEY"]) and rotate the exposed key.',
)
# Provider-specific remediation and placeholder notes (ADR-037): Groq names GROQ_API_KEY
# (contract A3); BharatCode keeps its Phase 4 wording until G6.
_FIX = {
    "groq": (
        f"Read the key from the {GROQ_API_KEY_ENV} environment variable "
        f'(os.environ["{GROQ_API_KEY_ENV}"]; the groq SDK reads it by default) and rotate the '
        "exposed key at console.groq.com/keys."
    ),
    "bharatcode": (
        "Read the key from the BHARATCODE_API_KEY environment variable "
        '(os.environ["BHARATCODE_API_KEY"]) and rotate the exposed key.'
    ),
}
_ENV_NAME = {"groq": GROQ_API_KEY_ENV, "bharatcode": "BHARATCODE_API_KEY"}
_PREFIX_PROVIDER = {"gsk_": "groq", "bc_live_": "bharatcode"}


def _placeholder_note(provider: str | None) -> str:
    where = _ENV_NAME.get(provider or "", "your provider's API-key variable")
    return (
        "looks like a placeholder, not a real key; make sure the real key comes from the "
        f"environment ({where})."
    )


def _client_provider_at(ctx: RuleContext, file: FileIndex, loc: Location) -> str | None:
    """The provider of a client constructor whose arguments contain ``loc``."""
    for binding in file.clients:
        start = binding.location.line
        end = binding.location.end_line or start
        if start <= loc.line <= end:
            detection = ctx.detection.bindings.get(binding.id)
            if detection is not None and detection.provider in _FIX:
                return detection.provider
    return None


def _key_provider(
    ctx: RuleContext, file: FileIndex, loc: Location, value: str, name: str | None = None
) -> str | None:
    """``groq``/``bharatcode`` when the key's prefix, name or client says so, else ``None``."""
    lowered = value.strip().lower()
    for prefix, provider in _PREFIX_PROVIDER.items():
        if lowered.startswith(prefix):
            return provider
    if name is not None:
        if is_groq_env_name(name):
            return "groq"
        if "BHARATCODE" in name.upper():
            return "bharatcode"
    return _client_provider_at(ctx, file, loc)


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
            provider = _PREFIX_PROVIDER.get(prefix) or _client_provider_at(ctx, file, loc)
            fix = _FIX.get(provider or "")
            if is_placeholder_key(literal.value):
                yield ctx.finding_at(
                    SEC001,
                    loc,
                    f"String literal with the `{prefix}` key prefix {_placeholder_note(provider)}",
                    severity=Severity.INFO,
                    remediation=fix,
                )
                continue
            yield ctx.finding_at(
                SEC001,
                loc,
                f"Hard-coded API key `{masked}`: a string literal with the `{prefix}` key "
                "prefix is in source code. Anyone with the code has the key.",
                remediation=fix,
            )
        for keyed in file.keyed_literals:
            loc = keyed.location
            if (loc.line, loc.column) in reported:
                continue
            reported.add((loc.line, loc.column))
            provider = _key_provider(ctx, file, loc, keyed.value, keyed.key)
            fix = _FIX.get(provider or "")
            if is_placeholder_key(keyed.value):
                yield ctx.finding_at(
                    SEC001,
                    loc,
                    f"Literal given to `{keyed.key}` {_placeholder_note(provider)}",
                    severity=Severity.INFO,
                    confidence=Confidence.MEDIUM,
                    remediation=fix,
                )
                continue
            yield ctx.finding_at(
                SEC001,
                loc,
                f"Hard-coded API key: a string literal is given to `{keyed.key}` "
                "(value redacted). Keys in source code leak with the code.",
                severity=Severity.HIGH,
                confidence=Confidence.MEDIUM,
                remediation=fix,
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
