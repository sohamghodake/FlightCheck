"""BharatCode adapter (plan §14.7): raw HTTP over httpx2 (ADR-003), synchronous (ADR-016).

What it adds on top of plain OpenAI-shaped HTTP, all from BHARATCODE_CONTRACT.md §15.1:

- ``store: false`` is always sent on ``/responses``; ``store: true``, ``previous_response_id``,
  hosted tools, ``background`` and audio/video/file input are refused locally before any
  network call (the documented 400s; same checks as the mock, ``core.contract``);
- the output budget is clamped to 32 000 with a logged warning; ``reasoning: {"effort": ...}``
  / ``reasoning_effort`` are passed through (only ``"none"`` is documented);
- a warning for models outside the documented catalogue;
- ``status: "incomplete"`` / ``finish_reason: "length"`` → ``TRUNCATED`` and tool calls marked
  incomplete; 503 → ``BUSY`` (retried); ``x-request-id`` captured on every answer and error;
- streams end only on a terminal event; ``response.failed`` and EOF without a terminal event
  are yielded as ``failed`` and then raised as ``ProviderError(STREAM_FAILED)``.

Every exception an operation raises is a :class:`ProviderError` with a redacted message.
Construction raises :class:`NetworkDisabledError` under ``FLIGHTCHECK_NO_NETWORK`` unless a
transport is injected, and :class:`ConfigError` when the API key is not set.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import httpx2
from pydantic import ValidationError

from flightcheck.core.capabilities import UNSUPPORTED_LEVELS, Capability, SupportLevel
from flightcheck.core.config import (
    BHARATCODE_DOCUMENTED_MODELS,
    BHARATCODE_PRO_ONLY_MODEL,
    MAX_OUTPUT_TOKENS_CAP,
    ProviderConfig,
)
from flightcheck.core.contract import Violation, responses_contract_violations
from flightcheck.core.errors import ConfigError, ErrorKind, ProviderError
from flightcheck.core.logging import get_logger
from flightcheck.core.models import Endpoint
from flightcheck.core.redact import redact, redact_obj
from flightcheck.providers.base import (
    ChatRequest,
    FinishState,
    ModelInfo,
    NormalizedResponse,
    ProviderAdapter,
    RawResponse,
    ResponsesRequest,
    StreamEvent,
    ToolCall,
    Usage,
    chat_finish_state,
    responses_finish_state,
)
from flightcheck.providers.capabilities import bharatcode_profile
from flightcheck.providers.http import (
    HTTPX_ERRORS,
    build_client,
    error_from_response,
    error_from_transport,
    parse_json_body,
    request_id_of,
    send_with_retry,
)
from flightcheck.providers.sse import (
    DONE_WITHOUT_FINISH_REASON,
    EOF_REASON,
    ERROR_CHUNK_REASON,
    UNRECOGNISED_FINISH_REASON,
    SSEMessage,
    SSEProtocolError,
    chat_events,
    iter_sse_lines,
    parse_sse,
    responses_events,
)

__all__ = ["BharatCodeAdapter"]

_log = get_logger("providers.bharatcode")

_PATHS = {Endpoint.RESPONSES: "responses", Endpoint.CHAT_COMPLETIONS: "chat/completions"}
_ENDPOINT_FOR_PATH = {path: endpoint for endpoint, path in _PATHS.items()}
_BUDGET_FIELD = {Endpoint.RESPONSES: "max_output_tokens", Endpoint.CHAT_COMPLETIONS: "max_tokens"}
_UNSUPPORTED_CODE_PREFIX = "unsupported"
_EOF_MESSAGES = {
    EOF_REASON: "the stream ended without a terminal event",
    DONE_WITHOUT_FINISH_REASON: "the stream sent [DONE] without any finish_reason",
}
_NORMALISE_ERRORS = (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError)
_Mapper = Callable[[Iterable[SSEMessage]], Iterator[StreamEvent]]


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _contains_type(value: Any, wanted: str, depth: int = 0) -> bool:
    if depth > 20:
        return False
    if isinstance(value, dict):
        if value.get("type") == wanted:
            return True
        return any(_contains_type(child, wanted, depth + 1) for child in value.values())
    if isinstance(value, list):
        return any(_contains_type(child, wanted, depth + 1) for child in value)
    return False


def _has_function_tools(tools: Any) -> bool:
    return isinstance(tools, list) and any(
        isinstance(tool, dict) and tool.get("type") == "function" for tool in tools
    )


def capabilities_used(payload: dict[str, Any], endpoint: Endpoint) -> list[Capability]:
    """The capabilities a request payload relies on (documented-unsupported features are
    covered by the contract checks instead)."""
    stream = payload.get("stream") is True
    used: list[Capability] = []
    if endpoint is Endpoint.RESPONSES:
        used.append(Capability.RESPONSES_API)
        if stream:
            used.append(Capability.STREAM_RESPONSES)
        text = payload.get("text")
        if isinstance(text, dict) and text.get("format") is not None:
            used.append(Capability.STRUCTURED_OUTPUT_TEXT_FORMAT)
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("effort") == "none":
            used.append(Capability.REASONING_EFFORT_NONE)
        if _contains_type(payload.get("input"), "input_image"):
            used.append(Capability.IMAGE_INPUT)
    else:
        used.append(Capability.CHAT_COMPLETIONS)
        if stream:
            used.append(Capability.STREAM_CHAT)
        if payload.get("response_format") is not None:
            used.append(Capability.STRUCTURED_OUTPUT_RESPONSE_FORMAT)
        if payload.get("reasoning_effort") == "none":
            used.append(Capability.REASONING_EFFORT_NONE)
    if _has_function_tools(payload.get("tools")):
        used.append(Capability.FUNCTION_TOOLS)
    return used


def _safe_path(path: str) -> str:
    """A path relative to the base URL. Absolute URLs, ``//host`` and ``..`` are refused so the
    key can only ever go to the configured host."""
    relative = path.lstrip("/")
    first = relative.split("/")[0].split("?")[0]
    if (
        ":" in first  # a scheme ("https:", "mailto:") that httpx2 would silently drop
        or path.startswith("//")
        or "\\" in path
        or any(part == ".." for part in relative.split("?")[0].split("/"))
    ):
        raise ProviderError(
            ErrorKind.BAD_REQUEST, "raw_post needs a path relative to the provider base URL."
        )
    return relative


class BharatCodeAdapter(ProviderAdapter):
    """Synchronous BharatCode client. ``transport`` and ``sleep`` are injected by tests
    (ADR-016); ``jitter`` returns the backoff multiplier (default uniform 0.8–1.2)."""

    name = "bharatcode"

    def __init__(
        self,
        config: ProviderConfig,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        *,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.profile = bharatcode_profile(config.base_url, config.declared_capabilities)
        self._sleep = sleep
        self._jitter = jitter
        self._warned_models: set[str] = set()
        self._client = build_client(config.base_url, config.api_key, config.timeout, transport)
        if config.api_key is None or not config.api_key.get_secret_value().strip():
            self._client.close()
            raise ConfigError(
                f"No API key for provider '{config.name}': {config.api_key_env} is not set.",
                hint=f"Export {config.api_key_env} in the environment (never in a file).",
            )

    def __repr__(self) -> str:
        return f"BharatCodeAdapter(name={self.config.name!r}, base_url={self.config.base_url!r})"

    def close(self) -> None:
        self._client.close()

    # --- Payloads ---------------------------------------------------------------------------

    def _clamp_budget(self, field: str, value: int) -> int:
        if value > MAX_OUTPUT_TOKENS_CAP:
            _log.warning(
                "%s=%d exceeds BharatCode's documented limit; clamped to %d.",
                field,
                value,
                MAX_OUTPUT_TOKENS_CAP,
            )
            return MAX_OUTPUT_TOKENS_CAP
        return value

    def responses_payload(self, req: ResponsesRequest, *, stream: bool = False) -> dict[str, Any]:
        """The ``/responses`` JSON body. Typed fields win over ``extra_body``."""
        payload = copy.deepcopy(req.extra_body)
        payload["model"] = req.model
        payload["input"] = copy.deepcopy(req.input)
        if req.instructions is not None:
            payload["instructions"] = req.instructions
        if req.tools is not None:
            payload["tools"] = copy.deepcopy(req.tools)
        if req.tool_choice is not None:
            payload["tool_choice"] = copy.deepcopy(req.tool_choice)
        if req.text_format is not None:
            text = payload.get("text") if isinstance(payload.get("text"), dict) else {}
            payload["text"] = {**text, "format": copy.deepcopy(req.text_format)}
        if req.max_output_tokens is not None:
            payload["max_output_tokens"] = self._clamp_budget(
                "max_output_tokens", req.max_output_tokens
            )
        if req.reasoning_effort is not None:
            # Only "none" is documented; other values are [UNVERIFIED] and passed through.
            payload["reasoning"] = {"effort": req.reasoning_effort}
        # Always sent. store=True is the caller's explicit choice and preflight refuses it.
        payload["store"] = req.store
        if stream:
            payload["stream"] = True
        else:
            payload.pop("stream", None)
        return payload

    def chat_payload(self, req: ChatRequest, *, stream: bool = False) -> dict[str, Any]:
        """The ``/chat/completions`` JSON body. Typed fields win over ``extra_body``."""
        payload = copy.deepcopy(req.extra_body)
        payload["model"] = req.model
        payload["messages"] = copy.deepcopy(req.messages)
        if req.tools is not None:
            payload["tools"] = copy.deepcopy(req.tools)
        if req.tool_choice is not None:
            payload["tool_choice"] = copy.deepcopy(req.tool_choice)
        if req.max_tokens is not None:
            payload["max_tokens"] = self._clamp_budget("max_tokens", req.max_tokens)
        if req.reasoning_effort is not None:
            payload["reasoning_effort"] = req.reasoning_effort  # only "none" is documented
        if stream:
            payload["stream"] = True
        else:
            payload.pop("stream", None)
        return payload

    # --- Pre-flight -------------------------------------------------------------------------

    def _violations(self, payload: dict[str, Any], endpoint: Endpoint) -> list[Violation]:
        found: list[Violation] = []
        model = payload.get("model")
        if not isinstance(model, str) or not model.strip():
            found.append(
                Violation("model", "model_required", "model is required on every request.")
            )
        if endpoint is Endpoint.RESPONSES:
            found.extend(responses_contract_violations(payload))
        budget_field = _BUDGET_FIELD.get(endpoint)
        budget = _int_or_none(payload.get(budget_field)) if budget_field else None
        if budget is not None and budget > MAX_OUTPUT_TOKENS_CAP:
            found.append(
                Violation(
                    budget_field or "",
                    "output_budget_exceeded",
                    f"{budget_field}={budget} is above the documented limit of "
                    f"{MAX_OUTPUT_TOKENS_CAP}.",
                )
            )
        return found

    def _warn_about_model(self, model: Any) -> None:
        if not isinstance(model, str) or model in self._warned_models:
            return
        self._warned_models.add(model)
        if model not in BHARATCODE_DOCUMENTED_MODELS:
            _log.warning(
                "Model %r is not in BharatCode's documented catalogue (%s); GET /models lists "
                "the models your key can use.",
                redact(model),
                ", ".join(sorted(BHARATCODE_DOCUMENTED_MODELS)),
            )
        elif model == BHARATCODE_PRO_ONLY_MODEL:
            _log.info("Model %r is documented as Pro only.", model)

    def preflight(self, payload: dict[str, Any], endpoint: Endpoint) -> list[str]:
        """Contract violations in ``payload`` (BHARATCODE_CONTRACT.md §15.1); empty = OK.
        Also logs a warning for a model outside the documented catalogue."""
        self._warn_about_model(payload.get("model"))
        return [violation.message for violation in self._violations(payload, endpoint)]

    def unverified_capabilities(
        self, payload: dict[str, Any], endpoint: Endpoint
    ) -> list[Capability]:
        """Capabilities the payload uses that are only ``unknown`` or user-``declared``."""
        unverified = {SupportLevel.UNKNOWN, SupportLevel.DECLARED}
        return [
            cap
            for cap in capabilities_used(payload, endpoint)
            if self.profile.level(cap) in unverified
        ]

    def _enforce(self, payload: dict[str, Any], endpoint: Endpoint) -> None:
        """Refuse locally, before any network call, what the contract or profile rules out."""
        self._warn_about_model(payload.get("model"))
        violations = self._violations(payload, endpoint)
        refused = [
            cap
            for cap in capabilities_used(payload, endpoint)
            if self.profile.level(cap) in UNSUPPORTED_LEVELS
        ]
        if not violations and not refused:
            return
        problems = [violation.message for violation in violations]
        problems += [
            f"{cap.value} is marked {self.profile.level(cap).value} for this provider "
            f"({self.profile.entry(cap).source})."
            for cap in refused
        ]
        unsupported = refused or any(
            v.code.startswith(_UNSUPPORTED_CODE_PREFIX) for v in violations
        )
        raise ProviderError(
            ErrorKind.UNSUPPORTED_CAPABILITY if unsupported else ErrorKind.BAD_REQUEST,
            "Not sent to BharatCode (refused locally, documented contract): " + " ".join(problems),
            hint="See docs/BHARATCODE_CONTRACT.md §15.1.",
        )

    # --- HTTP -------------------------------------------------------------------------------

    @staticmethod
    def _encode(payload: dict[str, Any]) -> bytes:
        try:
            return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                ErrorKind.BAD_REQUEST,
                f"The request payload is not JSON-serialisable ({type(exc).__name__}).",
            ) from None

    def _send(
        self,
        method: str,
        path: str,
        content: bytes | None,
        *,
        stream: bool = False,
    ) -> httpx2.Response:
        """One attempt: send, and raise :class:`ProviderError` for non-2xx answers."""
        headers = {"content-type": "application/json"} if content is not None else {}
        if stream:
            headers["accept"] = "text/event-stream"
        try:
            request = self._client.build_request(method, path, content=content, headers=headers)
            response = self._client.send(request, stream=stream)
        except HTTPX_ERRORS as exc:
            raise error_from_transport(exc, provider=self.name) from exc
        if 200 <= response.status_code < 300:
            return response
        try:
            text = response.read().decode("utf-8", errors="replace") if stream else response.text
        except HTTPX_ERRORS:
            text = ""
        finally:
            response.close()
        raise error_from_response(response.status_code, response.headers, text, provider=self.name)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        attempts: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], httpx2.Response]:
        content = self._encode(payload) if payload is not None else None
        response = send_with_retry(
            lambda: self._send(method, path, content),
            self.config.retry,
            self._sleep,
            attempts=attempts,
            **({"jitter": self._jitter} if self._jitter else {}),
        )
        return parse_json_body(response, provider=self.name), response

    def _malformed(self, response: httpx2.Response, exc: Exception) -> ProviderError:
        request_id = request_id_of(response)
        detail = f"{type(exc).__name__}: {exc}"
        message = (
            f"{self.name} answered HTTP {response.status_code} with an unexpected body ({detail})"
        )
        if request_id:
            message += f" (x-request-id: {request_id})"
        return ProviderError(
            ErrorKind.MALFORMED_RESPONSE,
            message,
            http_status=response.status_code,
            request_id=request_id,
        )

    # --- Operations -------------------------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        # GET /models is documented; the {"data": [{"id": ...}]} body shape is OpenAI's and
        # [UNVERIFIED] for BharatCode.
        attempts: list[dict[str, Any]] = []
        body, response = self._request_json("GET", "models", None, attempts)
        try:
            data = body["data"]
            if not isinstance(data, list):
                raise TypeError("data is not a list")
            return [ModelInfo(id=item["id"], raw=redact_obj(item)) for item in data]
        except _NORMALISE_ERRORS as exc:
            raise self._malformed(response, exc) from None

    def create_response(self, req: ResponsesRequest) -> NormalizedResponse:
        payload = self.responses_payload(req)
        self._enforce(payload, Endpoint.RESPONSES)
        attempts: list[dict[str, Any]] = []
        body, response = self._request_json("POST", "responses", payload, attempts)
        try:
            return self._normalise_responses(body, response, payload, attempts)
        except _NORMALISE_ERRORS as exc:
            raise self._malformed(response, exc) from None

    def create_chat_completion(self, req: ChatRequest) -> NormalizedResponse:
        payload = self.chat_payload(req)
        self._enforce(payload, Endpoint.CHAT_COMPLETIONS)
        attempts: list[dict[str, Any]] = []
        body, response = self._request_json("POST", "chat/completions", payload, attempts)
        try:
            return self._normalise_chat(body, response, payload, attempts)
        except _NORMALISE_ERRORS as exc:
            raise self._malformed(response, exc) from None

    def stream_response(self, req: ResponsesRequest) -> Iterator[StreamEvent]:
        """Pre-flight runs now; the request is sent when iteration starts."""
        payload = self.responses_payload(req, stream=True)
        self._enforce(payload, Endpoint.RESPONSES)
        return self._stream("responses", payload, responses_events)

    def stream_chat_completion(self, req: ChatRequest) -> Iterator[StreamEvent]:
        """Pre-flight runs now; the request is sent when iteration starts."""
        payload = self.chat_payload(req, stream=True)
        self._enforce(payload, Endpoint.CHAT_COMPLETIONS)
        return self._stream("chat/completions", payload, chat_events)

    def raw_post(
        self, path: str, json: dict[str, Any], allow_contract_violation: bool = False
    ) -> RawResponse:
        """POST ``json`` to ``path`` exactly as given, for probes (Phase 10).

        No payload rewriting, no clamping, no retries, and HTTP errors are returned rather than
        raised. Pre-flight still refuses documented contract violations on ``responses`` and
        ``chat/completions`` unless ``allow_contract_violation`` is True.
        """
        relative = _safe_path(path)
        endpoint = _ENDPOINT_FOR_PATH.get(relative.split("?")[0].rstrip("/"))
        if endpoint is not None and not allow_contract_violation:
            self._enforce(json, endpoint)
        content = self._encode(json)
        try:
            request = self._client.build_request(
                "POST", relative, content=content, headers={"content-type": "application/json"}
            )
            if not str(request.url).startswith(str(self._client.base_url)):
                raise ProviderError(
                    ErrorKind.BAD_REQUEST, "raw_post must stay under the provider base URL."
                )
            response = self._client.send(request)
        except HTTPX_ERRORS as exc:
            raise error_from_transport(exc, provider=self.name) from exc
        text = redact(response.text)
        try:
            body = redact_obj(response.json())
        except ValueError:
            body = None
        return RawResponse(
            http_status=response.status_code,
            request_id=request_id_of(response),
            headers={name.lower(): redact(value) for name, value in response.headers.items()},
            body=body,
            text=text,
        )

    # --- Normalisation ----------------------------------------------------------------------

    def _result(
        self,
        endpoint: Endpoint,
        body: dict[str, Any],
        response: httpx2.Response,
        payload: dict[str, Any],
        attempts: list[dict[str, Any]],
        **fields: Any,
    ) -> NormalizedResponse:
        model = body.get("model")
        raw = redact_obj(body)
        raw["_flightcheck_attempts"] = attempts
        return NormalizedResponse(
            provider=self.name,
            endpoint=endpoint,
            model=model if isinstance(model, str) else payload["model"],
            http_status=response.status_code,
            request_id=request_id_of(response),
            raw=raw,
            unverified_capabilities=self.unverified_capabilities(payload, endpoint),
            **fields,
        )

    def _normalise_responses(
        self,
        body: dict[str, Any],
        response: httpx2.Response,
        payload: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> NormalizedResponse:
        output = body["output"]
        if not isinstance(output, list):
            raise TypeError("output is not a list")
        texts: list[str] = []
        calls: list[tuple[Any, Any, Any, Any]] = []
        for item in output:
            if not isinstance(item, dict):
                raise TypeError("an output item is not an object")
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        text = part["text"]
                        if not isinstance(text, str):
                            raise TypeError("output_text.text is not a string")
                        texts.append(text)
            elif item.get("type") == "function_call":
                calls.append((item["call_id"], item["name"], item["arguments"], item.get("status")))
        status = body.get("status")
        state = responses_finish_state(status, bool(calls))
        details = body.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        detail = reason if isinstance(reason, str) else status if isinstance(status, str) else None
        tool_calls = [
            ToolCall(
                call_id=call_id,
                name=name,
                arguments_raw=arguments,
                complete=state is FinishState.TOOL_CALLS and item_status in (None, "completed"),
            )
            for call_id, name, arguments, item_status in calls
        ]
        usage = body.get("usage")
        return self._result(
            Endpoint.RESPONSES,
            body,
            response,
            payload,
            attempts,
            text="".join(texts),
            tool_calls=tool_calls,
            finish_state=state,
            finish_detail=detail,
            usage=self._usage(usage, "input_tokens", "output_tokens", "output_tokens_details"),
        )

    def _normalise_chat(
        self,
        body: dict[str, Any],
        response: httpx2.Response,
        payload: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> NormalizedResponse:
        choices = body["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("choices is empty or not a list")
        choice = choices[0]
        message = choice["message"]
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise TypeError("message.content is not a string")
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise TypeError("message.tool_calls is not a list")
        finish = choice.get("finish_reason")
        state = chat_finish_state(finish, bool(raw_calls))
        tool_calls = [
            ToolCall(
                call_id=call["id"],
                name=call["function"]["name"],
                arguments_raw=call["function"]["arguments"],
                complete=state is FinishState.TOOL_CALLS,
            )
            for call in raw_calls
        ]
        return self._result(
            Endpoint.CHAT_COMPLETIONS,
            body,
            response,
            payload,
            attempts,
            text=content or "",
            tool_calls=tool_calls,
            finish_state=state,
            finish_detail=finish if isinstance(finish, str) else None,
            usage=self._usage(
                body.get("usage"), "prompt_tokens", "completion_tokens", "completion_tokens_details"
            ),
        )

    @staticmethod
    def _usage(usage: Any, input_key: str, output_key: str, details_key: str) -> Usage | None:
        # Usage field names follow OpenAI ([UNVERIFIED] for BharatCode); missing or odd values
        # become None rather than an error.
        if not isinstance(usage, dict):
            return None
        details = usage.get(details_key)
        reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
        return Usage(
            input_tokens=_int_or_none(usage.get(input_key)),
            output_tokens=_int_or_none(usage.get(output_key)),
            reasoning_tokens=_int_or_none(reasoning),
        )

    # --- Streaming --------------------------------------------------------------------------

    def _open_stream(self, path: str, content: bytes) -> httpx2.Response:
        response = self._send("POST", path, content, stream=True)
        content_type = response.headers.get("content-type", "")
        if content_type.split(";")[0].strip().lower() == "application/json":
            # The server ignored stream:true. Whether BharatCode labels SSE answers with
            # text/event-stream is [UNVERIFIED], so only an explicit JSON answer is refused.
            request_id = request_id_of(response)
            response.close()
            raise ProviderError(
                ErrorKind.MALFORMED_RESPONSE,
                f"{self.name} answered a stream request with JSON instead of SSE"
                + (f" (x-request-id: {request_id})" if request_id else ""),
                http_status=response.status_code,
                request_id=request_id,
            )
        return response

    def _stream(self, path: str, payload: dict[str, Any], mapper: _Mapper) -> Iterator[StreamEvent]:
        """Retries happen only while opening the stream (before any byte of a 2xx answer);
        never once the stream has started (plan §14.5)."""
        content = self._encode(payload)
        response = send_with_retry(
            lambda: self._open_stream(path, content),
            self.config.retry,
            self._sleep,
            **({"jitter": self._jitter} if self._jitter else {}),
        )
        request_id = request_id_of(response)
        try:
            for event in mapper(parse_sse(iter_sse_lines(response.iter_text()))):
                yield event
                if event.kind == "failed":
                    raise self._stream_failed(event, response.status_code, request_id)
        except SSEProtocolError as exc:
            raise ProviderError(
                ErrorKind.MALFORMED_RESPONSE,
                self._suffix(f"{self.name} sent a malformed stream event: {exc}", request_id),
                http_status=response.status_code,
                request_id=request_id,
            ) from None
        except HTTPX_ERRORS as exc:
            cause = error_from_transport(exc, provider=self.name)
            raise ProviderError(
                cause.kind,
                self._suffix(f"The stream was interrupted: {cause.message}", request_id),
                http_status=response.status_code,
                request_id=request_id,
                hint=cause.hint,
            ) from exc
        finally:
            response.close()

    @staticmethod
    def _suffix(message: str, request_id: str | None) -> str:
        return f"{message} (x-request-id: {request_id})" if request_id else message

    def _stream_failed(
        self, event: StreamEvent, status: int, request_id: str | None
    ) -> ProviderError:
        data = event.data
        reason = data.get("reason")
        if event.raw_event == "response.failed":
            body = data.get("response")
            error = body.get("error") if isinstance(body, dict) else None
            error = error if isinstance(error, dict) else {}
            detail = error.get("message") if isinstance(error.get("message"), str) else "no details"
            code = error.get("code")
            what = f"response.failed: {detail}" + (f" [{code}]" if isinstance(code, str) else "")
        elif reason in _EOF_MESSAGES:
            what = f"{_EOF_MESSAGES[reason]} ({reason})"
        elif reason == UNRECOGNISED_FINISH_REASON:
            what = f"unrecognised finish_reason {data.get('finish_reason')!r}"
        elif reason == ERROR_CHUNK_REASON:
            error = data.get("error")
            detail = error.get("message") if isinstance(error, dict) else error
            what = f"error chunk: {detail}"
        else:
            what = "failed event"
        return ProviderError(
            ErrorKind.STREAM_FAILED,
            self._suffix(f"{self.name} stream failed ({what})", request_id),
            http_status=status,
            request_id=request_id,
        )
