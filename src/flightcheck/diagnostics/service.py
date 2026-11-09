"""The diagnostic service behind ``flightcheck explain`` (plan §19, ADR-019, ADR-020, ADR-031).

``DiagnosticService.prepare(report, options)`` selects context and builds every request
without any network access (it also backs ``--dry-run`` and the consent panel).
``DiagnosticService.explain(report, options)`` returns a *copy* of the report whose only
differences are ``ai_suggestions`` and ``summary.ai_suggestions``: findings, checks, statuses,
severities, metadata and every other count are untouched (ADR-019).

Backends: ``none`` (deterministic remediation), ``fake`` (offline, labelled), ``bharatcode``
(``POST /responses`` through the provider adapter, only after consent). Every failure ends in
the deterministic fallback with a ``fallback_reason``; nothing here raises except a missing
consent callback (:class:`ConsentRequiredError`, exit 2).
"""

from __future__ import annotations

import difflib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2
from pydantic import ValidationError

from flightcheck.core.config import MAX_OUTPUT_TOKENS_CAP, FlightCheckConfig, ProviderConfig
from flightcheck.core.errors import (
    ConfigError,
    ConsentRequiredError,
    ErrorKind,
    NetworkDisabledError,
    ProviderError,
)
from flightcheck.core.logging import get_logger
from flightcheck.core.models import AI_LABEL, AIExplanation, AISuggestion, Report
from flightcheck.core.redact import redact, redact_obj
from flightcheck.diagnostics.context import CheckCatalogue, Group, Selection, select
from flightcheck.diagnostics.fake_llm import FAKE_MODEL, FakeLLM
from flightcheck.diagnostics.prompt import build_input, build_request, preview_payload
from flightcheck.diagnostics.sanitize import Sanitizer
from flightcheck.diagnostics.schema import AIExplanationBatch
from flightcheck.providers.base import FinishState, ProviderAdapter, ResponsesRequest
from flightcheck.providers.registry import build_adapter

Backend = Literal["none", "fake", "bharatcode"]
StructuredMode = Literal["json_schema", "prompt_only", "n/a"]

INPUT_CAP_CHARS = 24_000  # [ASSUMPTION] per call (§19.7): context window not documented

# Fallback reasons (§19.7, §21.3, ADR-031).
NO_KEY = "no_key"
LLM_NONE = "llm_none"
NETWORK_DISABLED = "network_disabled"
CONSENT_DECLINED = "consent_declined"
PROVIDER_BUSY = "provider_busy"
AUTH_OR_PLAN = "auth_or_plan"
TRUNCATED = "truncated"
INVALID_OUTPUT = "invalid_output"
REJECTED = "rejected"
MISSING_EXPLANATION = "missing_explanation"
CALL_LIMIT = "call_limit"
INPUT_TOO_LARGE = "input_too_large"
TIMEOUT = "timeout"
CONNECTION_ERROR = "connection_error"
PROVIDER_ERROR = "provider_error"
INTERNAL_ERROR = "internal_error"

FALLBACK_REASONS = (
    NO_KEY,
    LLM_NONE,
    NETWORK_DISABLED,
    CONSENT_DECLINED,
    PROVIDER_BUSY,
    AUTH_OR_PLAN,
    TRUNCATED,
    INVALID_OUTPUT,
    REJECTED,
    MISSING_EXPLANATION,
    CALL_LIMIT,
    INPUT_TOO_LARGE,
    TIMEOUT,
    CONNECTION_ERROR,
    PROVIDER_ERROR,
    INTERNAL_ERROR,
)

_KIND_REASON = {
    ErrorKind.BUSY: PROVIDER_BUSY,
    ErrorKind.RATE_LIMITED: PROVIDER_BUSY,
    ErrorKind.AUTH: AUTH_OR_PLAN,
    ErrorKind.FORBIDDEN: AUTH_OR_PLAN,
    ErrorKind.TIMEOUT: TIMEOUT,
    ErrorKind.CONNECTION: CONNECTION_ERROR,
}

_FENCE = re.compile(r"^```[A-Za-z]*[ \t]*\n?(.*?)\n?```$", re.DOTALL)

_log = get_logger(__name__)

__all__ = [
    "FALLBACK_REASONS",
    "INPUT_CAP_CHARS",
    "CallPlan",
    "ConsentInfo",
    "DiagnosticService",
    "ExplainOptions",
    "ExplainPlan",
    "strip_code_fences",
    "unified_diff",
]


@dataclass(frozen=True)
class ConsentInfo:
    """What the consent panel shows (§19.3)."""

    destination: str  # full URL of POST /responses
    host: str
    model: str
    context: str
    groups: int
    findings: int  # instances sent
    characters: int  # instructions + input, all calls
    calls: int
    files: list[str]
    notes: list[str]


@dataclass(frozen=True)
class ExplainOptions:
    """Per-run options that are not configuration. ``consent`` is injected by the CLI and is
    asked only before data would leave the process (backend ``bharatcode``)."""

    repo: Path = Path(".")
    dry_run: bool = False
    consent: Callable[[ConsentInfo], bool] | None = None


@dataclass
class CallPlan:
    groups: list[Group]
    input_text: str
    request: ResponsesRequest

    @property
    def characters(self) -> int:
        return len(self.input_text) + len(self.request.instructions or "")


@dataclass
class ExplainPlan:
    backend: Backend
    reason: str | None  # decided before any call (no_key, llm_none); applies to every group
    selection: Selection
    calls: list[CallPlan]
    skipped: dict[str, str]  # rule_id -> call_limit | input_too_large
    model: str  # the BharatCode model the requests name
    destination: str

    @property
    def nothing_to_explain(self) -> bool:
        return not self.selection.groups

    @property
    def notes(self) -> list[str]:
        """Context notes (e.g. a snippet omitted because the file is not under --repo)."""
        return list(self.selection.notes)

    @property
    def needs_consent(self) -> bool:
        return self.backend == "bharatcode" and self.reason is None and bool(self.calls)

    @property
    def host(self) -> str:
        return urlsplit(self.destination).hostname or self.destination

    def consent_info(self) -> ConsentInfo:
        return ConsentInfo(
            destination=self.destination,
            host=self.host,
            model=self.model,
            context=self.selection.level,
            groups=sum(len(call.groups) for call in self.calls),
            findings=sum(len(g.instances) for call in self.calls for g in call.groups),
            characters=sum(call.characters for call in self.calls),
            calls=len(self.calls),
            files=self.selection.files,
            notes=list(self.selection.notes),
        )

    def payloads(self) -> list[dict[str, Any]]:
        """The exact JSON bodies that ``--llm bharatcode`` would send (redacted again)."""
        return [redact_obj(preview_payload(call.request)) for call in self.calls]


@dataclass
class _Answer:
    text: str
    finish_state: FinishState
    request_id: str | None
    model: str | None


@dataclass
class _Outcome:
    explanation: AIExplanation | None = None
    diff: str | None = None
    fallback_reason: str | None = None
    rejected_reason: str | None = None
    request_id: str | None = None
    model: str | None = None
    structured_output: StructuredMode = "n/a"
    latency_ms: int | None = None


@dataclass
class _CallContext:
    backend: Backend
    level: str
    call: Callable[[ResponsesRequest], _Answer]
    model: str | None


def strip_code_fences(text: str) -> str:
    """Remove one surrounding Markdown code fence (```json … ```); the result is still parsed
    strictly. Prose around the JSON is not removed and fails validation."""
    stripped = text.strip()
    match = _FENCE.match(stripped)
    return match.group(1).strip() if match else stripped


def unified_diff(path: str, original: str, proposed: str) -> str:
    """Locally generated diff of an AI patch, labelled as unverified."""
    lines = difflib.unified_diff(
        original.splitlines(),
        proposed.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return f"# {AI_LABEL}\n" + "\n".join(lines)


class DiagnosticService:
    """``transport``/``sleep`` are injected by tests (ADR-016); ``catalogue`` maps runtime
    check IDs to (title, remediation) and is injected by the orchestrator (ADR-031)."""

    def __init__(
        self,
        config: FlightCheckConfig,
        *,
        catalogue: CheckCatalogue | None = None,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
        input_cap: int = INPUT_CAP_CHARS,
    ) -> None:
        self.config = config
        self.catalogue = catalogue or {}
        self._transport = transport
        self._sleep = sleep
        self._now = now
        self._clock = clock
        self._input_cap = input_cap

    @property
    def provider(self) -> ProviderConfig:
        return self.config.providers[self.config.explain.provider]

    # --- Planning (no network) --------------------------------------------------------------

    def _backend(self) -> tuple[Backend, str | None]:
        has_key = self.provider.api_key is not None and bool(
            self.provider.api_key.get_secret_value().strip()
        )
        llm = self.config.explain.llm
        if llm is None:  # default: bharatcode when a key is set (§23.2)
            return ("bharatcode", None) if has_key else ("none", NO_KEY)
        if llm == "none":
            return "none", LLM_NONE
        if llm == "fake":
            return "fake", None
        return "bharatcode", None if has_key else NO_KEY

    def prepare(self, report: Report, options: ExplainOptions) -> ExplainPlan:
        cfg = self.config.explain
        backend, reason = self._backend()
        sanitizer = Sanitizer(options.repo)
        selection = select(
            report,
            level=cfg.context,
            max_groups=cfg.max_groups,
            repo=options.repo,
            catalogue=self.catalogue,
            sanitizer=sanitizer,
        )
        model = cfg.model or self.provider.default_model
        calls, skipped = self._pack(selection, sanitizer, model)
        return ExplainPlan(
            backend=backend,
            reason=reason,
            selection=selection,
            calls=calls,
            skipped=skipped,
            model=model,
            destination=self.provider.base_url.rstrip("/") + "/responses",
        )

    def _request(
        self, input_text: str, model: str, *, structured: bool = True, budget: int | None = None
    ) -> ResponsesRequest:
        cfg = self.config.explain
        return build_request(
            input_text,
            model=model,
            max_output_tokens=budget or cfg.max_output_tokens,
            reasoning_none=cfg.reasoning_none,
            structured=structured,
        )

    def _pack(
        self, selection: Selection, sanitizer: Sanitizer, model: str
    ) -> tuple[list[CallPlan], dict[str, str]]:
        """Greedy split of groups into calls of at most ``input_cap`` input characters; at
        most ``max_calls`` calls (§19.7). A group that alone exceeds the cap is skipped."""
        level = selection.level
        batches: list[list[Group]] = []
        skipped: dict[str, str] = {}
        current: list[Group] = []
        for group in selection.groups:
            if len(build_input([group], level, sanitizer)) > self._input_cap:
                skipped[group.rule_id] = INPUT_TOO_LARGE
                continue
            if current and len(build_input([*current, group], level, sanitizer)) > self._input_cap:
                batches.append(current)
                current = []
            current.append(group)
        if current:
            batches.append(current)
        max_calls = self.config.explain.max_calls
        for batch in batches[max_calls:]:
            for group in batch:
                skipped[group.rule_id] = CALL_LIMIT
        calls = []
        for batch in batches[:max_calls]:
            text = build_input(batch, level, sanitizer)
            calls.append(CallPlan(batch, text, self._request(text, model)))
        return calls, skipped

    # --- Explaining -------------------------------------------------------------------------

    def explain(
        self, report: Report, options: ExplainOptions, *, plan: ExplainPlan | None = None
    ) -> Report:
        """A copy of ``report`` with ``ai_suggestions`` filled in (one per selected group).

        ``--dry-run`` returns an unchanged copy without asking consent or calling anything.
        """
        plan = plan or self.prepare(report, options)
        if options.dry_run:
            return report.model_copy(deep=True)
        outcomes: dict[str, _Outcome] = {}
        if plan.reason is not None:
            outcomes = {
                g.rule_id: _Outcome(fallback_reason=plan.reason) for g in plan.selection.groups
            }
        elif plan.backend == "fake":
            fake = FakeLLM()
            ctx = _CallContext(
                "fake",
                plan.selection.level,
                lambda req: _Answer(fake.respond(req), FinishState.COMPLETE, None, FAKE_MODEL),
                FAKE_MODEL,
            )
            outcomes = self._run_calls(plan, ctx)
        elif plan.needs_consent:
            if options.consent is None:
                raise ConsentRequiredError(
                    f"explain would send data to {plan.host}, and no consent step is available.",
                    hint="Review the payload with --dry-run, then re-run with --yes.",
                )
            if not options.consent(plan.consent_info()):
                outcomes = {
                    g.rule_id: _Outcome(fallback_reason=CONSENT_DECLINED)
                    for g in plan.selection.groups
                }
            else:
                outcomes = self._run_bharatcode(plan)
        else:  # bharatcode with nothing to send (every group skipped)
            outcomes = {}
        return self._assemble(report, plan, outcomes)

    def _run_bharatcode(self, plan: ExplainPlan) -> dict[str, _Outcome]:
        try:
            adapter = build_adapter(self.provider, transport=self._transport, sleep=self._sleep)
        except NetworkDisabledError:
            return {
                g.rule_id: _Outcome(fallback_reason=NETWORK_DISABLED) for g in plan.selection.groups
            }
        except ConfigError as exc:
            # The key was checked in _backend, so this is a provider kind without an adapter
            # (the generic OpenAI-compatible adapter is P1); a missing key stays no_key.
            has_key = self.provider.api_key is not None
            reason = PROVIDER_ERROR if has_key else NO_KEY
            detail = redact(exc.message) if has_key else None
            return {
                g.rule_id: _Outcome(fallback_reason=reason, rejected_reason=detail)
                for g in plan.selection.groups
            }
        with adapter:
            ctx = _CallContext(
                "bharatcode", plan.selection.level, _adapter_call(adapter), plan.model
            )
            return self._run_calls(plan, ctx)

    def _run_calls(self, plan: ExplainPlan, ctx: _CallContext) -> dict[str, _Outcome]:
        outcomes: dict[str, _Outcome] = {}
        for call in plan.calls:
            try:
                outcomes.update(self._run_call(call, plan, ctx))
            except Exception as exc:  # never crash: any surprise becomes a fallback
                _log.warning("explain call failed unexpectedly: %s", redact(repr(exc)))
                for group in call.groups:
                    outcomes[group.rule_id] = _Outcome(
                        fallback_reason=INTERNAL_ERROR, rejected_reason=type(exc).__name__
                    )
        return outcomes

    def _run_call(
        self, call: CallPlan, plan: ExplainPlan, ctx: _CallContext
    ) -> dict[str, _Outcome]:
        structured = ctx.backend == "bharatcode"
        mode: StructuredMode = "json_schema" if structured else "n/a"
        budget = self.config.explain.max_output_tokens
        format_retry_done = budget_retry_done = False

        def fail(reason: str, detail: str | None = None, **extra: Any) -> dict[str, _Outcome]:
            base = {"model": ctx.model, "structured_output": mode, **extra}
            return {
                g.rule_id: _Outcome(fallback_reason=reason, rejected_reason=detail, **base)
                for g in call.groups
            }

        while True:
            request = self._request(
                call.input_text, plan.model, structured=structured, budget=budget
            )
            started = self._clock()
            try:
                answer = ctx.call(request)
            except ProviderError as error:
                format_refused = error.http_status == 400 or (
                    error.kind is ErrorKind.UNSUPPORTED_CAPABILITY
                )
                if structured and format_refused and not format_retry_done:
                    # text.format sub-schema is [UNVERIFIED] (P-07): retry once without it.
                    structured, mode, format_retry_done = False, "prompt_only", True
                    continue
                reason = _KIND_REASON.get(error.kind, PROVIDER_ERROR)
                detail = f"{error.kind.value}: {error.message}"
                return fail(reason, redact(detail), request_id=error.request_id)
            latency_ms = int((self._clock() - started) * 1000)
            meta = {"request_id": answer.request_id, "latency_ms": latency_ms}
            if answer.model:
                meta["model"] = answer.model
            if answer.finish_state is FinishState.TRUNCATED:
                doubled = min(budget * 2, MAX_OUTPUT_TOKENS_CAP)
                if not budget_retry_done and doubled > budget:
                    # Documented: raise the budget; never retry the same exhausted one.
                    budget, budget_retry_done = doubled, True
                    continue
                return fail(TRUNCATED, f"output truncated at max_output_tokens={budget}", **meta)
            if answer.finish_state is not FinishState.COMPLETE:
                return fail(INVALID_OUTPUT, f"finish_state {answer.finish_state.value}", **meta)
            try:
                batch = AIExplanationBatch.model_validate_json(strip_code_fences(answer.text))
            except ValidationError as exc:
                return fail(
                    INVALID_OUTPUT,
                    f"output is not a valid explanation batch ({exc.error_count()} error(s))",
                    **meta,
                )
            results = validate_batch(batch, call.groups, ctx.level)
            out: dict[str, _Outcome] = {}
            for group in call.groups:
                explanation, diff, rejected = results.get(group.rule_id, (None, None, None))
                if explanation is not None:
                    out[group.rule_id] = _Outcome(
                        explanation=explanation,
                        diff=diff,
                        structured_output=mode,
                        model=meta.get("model", ctx.model),
                        request_id=answer.request_id,
                        latency_ms=latency_ms,
                    )
                else:
                    out[group.rule_id] = _Outcome(
                        fallback_reason=REJECTED if rejected else MISSING_EXPLANATION,
                        rejected_reason=rejected,
                        structured_output=mode,
                        model=meta.get("model", ctx.model),
                        request_id=answer.request_id,
                        latency_ms=latency_ms,
                    )
            return out

    def _assemble(self, report: Report, plan: ExplainPlan, outcomes: dict[str, _Outcome]) -> Report:
        generated_at = self._now()
        suggestions: list[AISuggestion] = []
        for group in plan.selection.groups:
            outcome = outcomes.get(group.rule_id)
            if outcome is None:
                reason = plan.skipped.get(group.rule_id, MISSING_EXPLANATION)
                outcome = _Outcome(fallback_reason=reason)
            backend = plan.backend
            model = outcome.model
            if model is None and backend == "bharatcode" and outcome.fallback_reason != NO_KEY:
                model = plan.model
            if backend == "fake":
                model = FAKE_MODEL
            suggestions.append(
                AISuggestion(
                    rule_id=group.rule_id,
                    title=group.title,
                    severity=group.severity,
                    finding_ids=group.instance_ids,
                    remediation=group.remediation,
                    explanation=outcome.explanation,
                    backend=backend,
                    model=model if backend != "none" else None,
                    request_id=outcome.request_id,
                    structured_output=outcome.structured_output,
                    latency_ms=outcome.latency_ms,
                    diff=outcome.diff,
                    fallback_reason=outcome.fallback_reason,
                    rejected_reason=outcome.rejected_reason,
                    generated_at=generated_at,
                )
            )
        explained = sum(1 for s in suggestions if s.explanation is not None)
        copy = report.model_copy(deep=True)
        copy.ai_suggestions = suggestions
        copy.summary = copy.summary.model_copy(update={"ai_suggestions": explained})
        return copy


def _adapter_call(adapter: ProviderAdapter) -> Callable[[ResponsesRequest], _Answer]:
    def call(request: ResponsesRequest) -> _Answer:
        response = adapter.create_response(request)
        return _Answer(response.text, response.finish_state, response.request_id, response.model)

    return call


def validate_batch(
    batch: AIExplanationBatch, groups: list[Group], level: str
) -> dict[str, tuple[AIExplanation | None, str | None, str | None]]:
    """§19.6 post-validation. ``{rule_id: (explanation, diff, rejected_reason)}`` for every
    rule ID the batch answered; an explanation for a rule that was not sent is dropped."""
    by_rule = {group.rule_id: group for group in groups}
    results: dict[str, tuple[AIExplanation | None, str | None, str | None]] = {}
    for item in batch.explanations:
        group = by_rule.get(item.rule_id)
        if group is None:
            _log.warning(
                "explain: dropped an explanation for rule %s, which was not sent.",
                redact(item.rule_id)[:80],
            )
            continue
        if item.rule_id in results:
            continue  # first answer per rule wins
        results[item.rule_id] = _validate_one(item, group, level)
    return results


def _validate_one(
    item: AIExplanation, group: Group, level: str
) -> tuple[AIExplanation | None, str | None, str | None]:
    sent = set(group.instance_ids)
    unknown = [fid for fid in item.finding_ids if fid not in sent]
    if not item.finding_ids:
        return None, None, "no finding_ids"
    if unknown:
        shown = ", ".join(redact(fid)[:40] for fid in unknown[:3])
        return None, None, f"finding_ids not in the group sent: {shown}"
    try:
        explanation = AIExplanation.model_validate(redact_obj(item.model_dump()))
    except ValidationError:
        return None, None, "invalid after redaction"
    diff = None
    patch = explanation.patch
    if patch is not None:
        if level != "snippets":
            return None, None, "patch proposed without snippets context"
        snippets = group.snippets_by_path().get(patch.path)
        if not snippets:
            return None, None, "patch path was not sent with a snippet"
        if not patch.original_snippet.strip() or not any(
            patch.original_snippet in snippet for snippet in snippets
        ):
            return None, None, "original_snippet is not verbatim in the snippet sent"
        diff = unified_diff(patch.path, patch.original_snippet, patch.proposed_snippet)
    return explanation, diff, None
