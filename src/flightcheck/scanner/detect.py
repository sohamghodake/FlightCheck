"""Provider detection (plan §16.1 step 4).

Each client binding and call site collects signals; the classification is the signal with the
highest confidence. Two ``high`` signals that disagree give ``unknown`` plus an ``info``
diagnostic. A *supporting* signal (a documented model name) never outweighs a direct signal of
the same confidence. Equal-confidence signals that still disagree resolve in the fixed order
``bharatcode`` > ``openai_compatible_unknown`` > ``openai_or_env`` > ``unknown`` (ADR-024).
Call sites whose receiver could not be resolved take the repo-level provider at ``low``
confidence (ADR-011).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from flightcheck.core.config import BHARATCODE_DOCUMENTED_MODELS
from flightcheck.core.models import Confidence, Diagnostic, Location, ProviderDetection
from flightcheck.scanner.parser import SDK_CONSTRUCTORS, CallSite, ClientBinding, RepoIndex

Provider = Literal["bharatcode", "openai", "openai_compatible_unknown", "openai_or_env", "unknown"]

BHARATCODE_HOST = "bharatcode.ai"
BHARATCODE_ENV_MARKER = "BHARATCODE"
# BHARATCODE_DOCUMENTED_MODELS (core.config) is re-exported here for existing callers.

_CONFIDENCE_RANK = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
_PROVIDER_ORDER: tuple[Provider, ...] = (
    "bharatcode",
    "openai",
    "openai_compatible_unknown",
    "openai_or_env",
    "unknown",
)
REPO_LOCATION_PATH = "."


@dataclass(frozen=True)
class Signal:
    provider: Provider
    confidence: Confidence
    text: str
    supporting: bool = False  # a hint only: never outweighs a direct signal of equal rank


@dataclass
class DetectionResult:
    bindings: dict[str, ProviderDetection] = field(default_factory=dict)  # ClientBinding.id
    call_sites: dict[str, ProviderDetection] = field(default_factory=dict)  # CallSite.id
    repo: ProviderDetection | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def as_list(self) -> list[ProviderDetection]:
        """Bindings, then call sites (each in index order), then the repo-level detection."""
        out = [*self.bindings.values(), *self.call_sites.values()]
        if self.repo is not None:
            out.append(self.repo)
        return out


def combine(signals: list[Signal]) -> tuple[Provider, Confidence, bool]:
    """Return ``(provider, confidence, conflict)`` for a non-empty list of signals."""
    best = max(_CONFIDENCE_RANK[s.confidence] for s in signals)
    ranked = [s for s in signals if _CONFIDENCE_RANK[s.confidence] == best]
    direct = [s for s in ranked if not s.supporting]
    top = {s.provider for s in (direct or ranked)}
    confidence = next(c for c, r in _CONFIDENCE_RANK.items() if r == best)
    if len(top) > 1 and confidence is Confidence.HIGH:
        return "unknown", Confidence.LOW, True
    provider = next(p for p in _PROVIDER_ORDER if p in top)
    return provider, confidence, False


def binding_signals(binding: ClientBinding, declared_provider: str | None = None) -> list[Signal]:
    signals: list[Signal] = []
    has_base_url = "base_url" in binding.kwargs
    base_literal = binding.kwargs.get("base_url")
    base_text = (
        base_literal if isinstance(base_literal, str) else "".join(binding.base_url_fragments)
    )
    base_env = binding.env_kwargs.get("base_url")
    key_env = binding.env_kwargs.get("api_key")
    bc_base = False
    if has_base_url and BHARATCODE_HOST in base_text.lower():
        bc_base = True
        signals.append(Signal("bharatcode", Confidence.HIGH, "base_url contains bharatcode.ai"))
    if base_env and BHARATCODE_ENV_MARKER in base_env.upper():
        bc_base = True
        signals.append(
            Signal("bharatcode", Confidence.HIGH, f"base_url read from env var {base_env}")
        )
    if key_env and BHARATCODE_ENV_MARKER in key_env.upper():
        signals.append(
            Signal("bharatcode", Confidence.MEDIUM, f"api_key read from env var {key_env}")
        )
    if has_base_url and not bc_base:
        how = "a literal" if isinstance(base_literal, str) else "a non-literal value"
        if base_env:
            how = f"env var {base_env}"
        signals.append(
            Signal(
                "openai_compatible_unknown",
                Confidence.MEDIUM,
                f"base_url set from {how} without bharatcode.ai",
            )
        )
    if not has_base_url:
        if binding.constructor in SDK_CONSTRUCTORS:
            signals.append(
                Signal(
                    "openai_or_env",
                    Confidence.LOW,
                    "no base_url: OpenAI SDK default or OPENAI_BASE_URL",
                )
            )
        else:
            signals.append(Signal("unknown", Confidence.LOW, "HTTP client without base_url"))
    signals.extend(_declared(declared_provider))
    return signals


def _declared(declared_provider: str | None) -> list[Signal]:
    if declared_provider == "bharatcode":
        return [Signal("bharatcode", Confidence.HIGH, "flightcheck.toml provider = bharatcode")]
    return []


def call_site_signals(call: CallSite) -> list[Signal]:
    """Signals visible at the call site itself (not inherited from its binding)."""
    signals: list[Signal] = []
    model = call.params.get("model")
    if isinstance(model, str):
        if model in BHARATCODE_DOCUMENTED_MODELS:
            signals.append(
                Signal(
                    "bharatcode",
                    Confidence.MEDIUM,
                    f"model {model!r} is a BharatCode model",
                    supporting=True,
                )
            )
    if call.kind == "http.post" and call.url and BHARATCODE_HOST in call.url.lower():
        signals.append(Signal("bharatcode", Confidence.HIGH, "raw HTTP post to bharatcode.ai"))
    return signals


def _detection(signals: list[Signal], location: Location) -> tuple[ProviderDetection, bool]:
    provider, confidence, conflict = combine(signals)
    texts = list(dict.fromkeys(s.text for s in signals))
    return (
        ProviderDetection(
            provider=provider, confidence=confidence, location=location, signals=texts
        ),
        conflict,
    )


def detect(index: RepoIndex, declared_provider: str | None = None) -> DetectionResult:
    """Classify every client binding, every call site, and the repository as a whole."""
    result = DetectionResult()
    all_signals: list[Signal] = []

    def conflict_note(location: Location, what: str) -> None:
        result.diagnostics.append(
            Diagnostic(
                level="info",
                code="provider_conflict",
                message=f"Conflicting high-confidence provider signals for {what} at line "
                f"{location.line}; classified as unknown.",
                path=location.path,
            )
        )

    for binding in index.clients:
        signals = binding_signals(binding, declared_provider)
        detection, conflict = _detection(signals, binding.location)
        if conflict:
            conflict_note(binding.location, "a client")
        result.bindings[binding.id] = detection
        all_signals.extend(signals)

    unresolved: list[CallSite] = []
    for call in index.call_sites:
        own = call_site_signals(call)
        if call.binding is None:
            unresolved.append(call)
            all_signals.extend(own)
            continue
        signals = [*binding_signals(call.binding, declared_provider), *own]
        detection, conflict = _detection(signals, call.location)
        if conflict:
            conflict_note(call.location, "a call site")
        result.call_sites[call.id] = detection
        all_signals.extend(own)

    if not all_signals and unresolved:
        all_signals.append(
            Signal("openai_or_env", Confidence.LOW, "no provider hints in the repository")
        )
    all_signals.extend(_declared(declared_provider))
    repo_provider: Provider | None = None
    if all_signals:
        repo, conflict = _detection(all_signals, Location(path=REPO_LOCATION_PATH, line=0))
        if conflict:
            conflict_note(repo.location, "the repository")
        result.repo = repo
        repo_provider = repo.provider

    for call in unresolved:
        own = call_site_signals(call)
        signals = list(own)
        if repo_provider is not None:
            signals.append(
                Signal(
                    repo_provider,
                    Confidence.LOW,
                    f"receiver not resolved; repo-level provider is {repo_provider}",
                )
            )
        detection, conflict = _detection(signals, call.location)
        if conflict:
            conflict_note(call.location, "a call site")
        result.call_sites[call.id] = detection

    # Keep call-site detections in index order (unresolved ones were added last).
    order = {call.id: i for i, call in enumerate(index.call_sites)}
    result.call_sites = dict(sorted(result.call_sites.items(), key=lambda kv: order[kv[0]]))
    return result


def detect_providers(
    index: RepoIndex, declared_provider: str | None = None
) -> list[ProviderDetection]:
    """Flat list for ``Report.providers_detected`` (see :func:`detect` for the full result)."""
    return detect(index, declared_provider).as_list()


__all__ = [
    "BHARATCODE_DOCUMENTED_MODELS",
    "DetectionResult",
    "Signal",
    "binding_signals",
    "call_site_signals",
    "combine",
    "detect",
    "detect_providers",
]
