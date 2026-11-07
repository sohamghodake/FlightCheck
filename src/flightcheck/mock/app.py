"""FastAPI app for the contract-aware BharatCode mock (plan §18.1; Phases 5A and 5B).

Order of checks per request (each failure is recorded and answered with an OpenAI-style error
body carrying ``x-request-id``; the body shape and codes are [UNVERIFIED] for BharatCode):

1. request limit (``max_requests``) → 429 ``flightcheck_request_limit``;
2. bearer token missing or blank → 401; a token other than ``fake_key`` is accepted and
   flagged (``response_summary["auth"] == "other_key"``, header stored as ``[REDACTED]``);
3. ``GET /models`` → the documented model IDs;
4. body not a JSON object → 400;
5. ``/responses`` only: the documented 400s (ADR-014) — ``store: true``,
   ``previous_response_id``, non-function (hosted) tools, ``background: true``, audio/file input;
6. the next scripted step from the :class:`FaultScript`, answered as JSON or, for
   ``stream: true``, as SSE (``text/event-stream``; ADR-015). ``HTTP_ERROR`` is a JSON error
   even on a stream request (it happens before the stream starts); ``DELAY`` waits before the
   first byte.

Only steps that reach 6 consume the script. Handler exceptions → 500 ``flightcheck_mock_error``
(recorded). Response bodies and SSE chunks are passed through ``core.redact`` before they are
sent. A stream is recorded when it ends, with its outcome (``finish``, ``chunks_sent``, ...) or
``finish: "client_disconnected"`` when the client went away first.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from flightcheck.core.contract import responses_contract_violations
from flightcheck.core.logging import get_logger
from flightcheck.core.models import Endpoint
from flightcheck.core.redact import redact, redact_obj
from flightcheck.mock import payloads
from flightcheck.mock.faults import FaultScript, MockAction, MockStep
from flightcheck.mock.recorder import TrafficRecorder, auth_status

DEFAULT_BASE_PATH = "/api/model/v1"
DEFAULT_MAX_REQUESTS = 25
MAX_DELAY_S = 600.0

_log = get_logger("mock")

# Which builder serves each action; DELAY and KEEPALIVE_STREAM answer with text when the
# request is not streamed.
_BUILDER_FOR_ACTION = {
    MockAction.OK_TEXT: "text",
    MockAction.KEEPALIVE_STREAM: "text",
    MockAction.DELAY: "text",
    MockAction.TOOL_CALL: "tool_call",
    MockAction.TRUNCATE: "truncated",
    MockAction.TRUNCATE_TOOL_CALL: "truncated_tool_call",
}
_TOOL_ACTIONS = frozenset({MockAction.TOOL_CALL, MockAction.TRUNCATE_TOOL_CALL})


# --- Request handling -----------------------------------------------------------------------


@dataclass
class _Exchange:
    """What is known about one request so far; recorded whatever happens."""

    seq: int
    endpoint: Endpoint | None
    raw_body: bytes = b""
    request_json: dict[str, Any] | None = None
    stream: bool = False
    step_id: str = "-"
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Answer:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class _StreamAnswer:
    stream: payloads.SSEStream


class _RecordingStreamingResponse(StreamingResponse):
    """Calls ``on_close`` once the response is over, however it ended (normal end, client
    disconnect, cancellation), so every stream is recorded exactly once."""

    def __init__(self, content: Any, on_close: Any, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._on_close()


def _error(status: int, message: str, code: str | None) -> _Answer:
    return _Answer(status, payloads.error_body(status, message, code))


def _summarize(endpoint: Endpoint, body: dict[str, Any]) -> dict[str, Any]:
    """Finish state and call IDs of a successful model answer."""
    if endpoint is Endpoint.RESPONSES:
        calls = [item for item in body["output"] if item["type"] == "function_call"]
        truncated = body["status"] == "incomplete"
        call_ids = [item["call_id"] for item in calls]
    else:
        choice = body["choices"][0]
        calls = choice["message"].get("tool_calls") or []
        truncated = choice["finish_reason"] == "length"
        call_ids = [call["id"] for call in calls]
    if calls:
        finish = "truncated_tool_call" if truncated else "tool_call"
    else:
        finish = "truncated" if truncated else "completed"
    return {"finish": finish, "call_ids": call_ids}


class _MockService:
    def __init__(
        self,
        script: FaultScript,
        recorder: TrafficRecorder,
        fake_key: str | None,
        max_requests: int,
    ) -> None:
        self.script = script
        self.recorder = recorder
        self.fake_key = fake_key
        self.max_requests = max_requests

    async def handle(
        self, request: Request, endpoint: Endpoint | None, route_status: int = 404
    ) -> Response:
        """Answer one request. ``endpoint=None`` is an unknown route, answered with
        ``route_status`` (404 or 405)."""
        ex = _Exchange(seq=self.recorder.next_seq(), endpoint=endpoint)
        try:
            answer = await self._dispatch(request, ex, route_status)
        except Exception as exc:  # noqa: BLE001 - every failure must become a recorded 500
            _log.warning("mock handler error on request %d: %s", ex.seq, type(exc).__name__)
            ex.summary["error"] = type(exc).__name__
            answer = _error(
                500, f"FlightCheck mock error ({type(exc).__name__}).", "flightcheck_mock_error"
            )
        if isinstance(answer, _StreamAnswer):
            return self._streaming_response(request, ex, answer.stream)
        error = answer.body.get("error")
        if isinstance(error, dict):
            ex.summary.setdefault("finish", "error")
            ex.summary["error_code"] = error.get("code")
        self._record(request, ex, answer.status)
        headers = {**answer.headers, **payloads.base_headers(ex.seq)}
        return JSONResponse(redact_obj(answer.body), status_code=answer.status, headers=headers)

    def _record(self, request: Request, ex: _Exchange, status: int) -> None:
        """Record the exchange. A recorder failure must never cost the client its answer (or
        its ``x-request-id``), so it is logged (type only) and counted instead."""
        try:
            self._record_unchecked(request, ex, status)
        except Exception as exc:  # noqa: BLE001
            self.recorder.note_record_failure()
            _log.warning("mock could not record request %d: %s", ex.seq, type(exc).__name__)

    def _record_unchecked(self, request: Request, ex: _Exchange, status: int) -> None:
        self.recorder.record(
            seq=ex.seq,
            method=request.method,
            path=request.url.path,
            endpoint=ex.endpoint,
            headers=request.headers,
            fake_key=self.fake_key,
            request_json=ex.request_json,
            raw_body=ex.raw_body,
            stream=ex.stream,
            step_id=ex.step_id,
            response_status=status,
            response_summary=ex.summary,
        )

    def _streaming_response(
        self, request: Request, ex: _Exchange, stream: payloads.SSEStream
    ) -> StreamingResponse:
        chunks = [redact(chunk.decode("utf-8")).encode("utf-8") for chunk in stream.chunks]
        state = {"sent": 0, "recorded": False}

        async def body() -> Any:
            for index, chunk in enumerate(chunks):
                if index and stream.interval_s:
                    await asyncio.sleep(stream.interval_s)  # scripted slow stream
                yield chunk
                state["sent"] += 1  # resumed only after the chunk was handed to the server
                await asyncio.sleep(0)  # let clients see incremental delivery

        def on_close() -> None:
            if state["recorded"]:
                return
            state["recorded"] = True
            # Complete = every chunk reached the server, even if the client left afterwards.
            if state["sent"] == len(chunks):
                ex.summary.update(stream.outcome)
            else:
                ex.summary.update(
                    {
                        "finish": "client_disconnected",
                        "planned_finish": stream.outcome.get("finish"),
                        "call_ids": stream.outcome.get("call_ids", []),
                    }
                )
            ex.summary["chunks_sent"] = state["sent"]
            ex.summary["chunks_planned"] = len(chunks)
            self._record(request, ex, 200)

        return _RecordingStreamingResponse(
            body(),
            on_close,
            status_code=200,
            headers=stream.headers,
            media_type="text/event-stream",
        )

    async def _dispatch(
        self, request: Request, ex: _Exchange, route_status: int
    ) -> _Answer | _StreamAnswer:
        if ex.seq > self.max_requests:
            ex.step_id = "request_limit"
            return _error(
                429,
                f"FlightCheck mock request limit reached ({self.max_requests} requests).",
                "flightcheck_request_limit",
            )
        if ex.endpoint is None:
            ex.step_id = "route"
            code = "method_not_allowed" if route_status == 405 else "not_found"
            return _error(route_status, "Unknown route for the FlightCheck mock.", code)
        auth = auth_status(request.headers.get("authorization"), self.fake_key)
        ex.summary["auth"] = auth
        if auth == "missing":
            ex.step_id = "auth"
            # The 401 body and code for BharatCode are [UNVERIFIED] (OpenAI's code is used).
            return _error(
                401, "Missing bearer token in the Authorization header.", "invalid_api_key"
            )
        if ex.endpoint is Endpoint.MODELS:
            ex.step_id = "models"
            return _Answer(200, payloads.models_list())

        ex.raw_body = await request.body()
        # BharatCode's answer to a malformed body is [UNVERIFIED]; OpenAI's 400 is assumed.
        try:
            parsed = await request.json()
        except ValueError:
            ex.step_id = "invalid_json"
            return _error(400, "Request body is not valid JSON.", "invalid_json")
        if not isinstance(parsed, dict):
            ex.step_id = "invalid_json"
            return _error(400, "Request body must be a JSON object.", "invalid_json")
        ex.request_json = parsed
        ex.stream = parsed.get("stream") is True

        if ex.endpoint is Endpoint.RESPONSES:
            violations = responses_contract_violations(parsed)
            if violations:
                ex.step_id = "contract"
                ex.summary["violations"] = [v.field for v in violations]
                return _error(400, violations[0].message, violations[0].code)
        step = self.script.next_step()
        ex.step_id = step.id
        ex.summary["action"] = str(step.action)
        return await self._serve(step, ex, parsed)

    async def _serve(
        self, step: MockStep, ex: _Exchange, request_json: dict[str, Any]
    ) -> _Answer | _StreamAnswer:
        assert ex.endpoint is not None  # only model endpoints reach a scripted step
        if step.action is MockAction.HTTP_ERROR:
            status, headers, body = payloads.http_error(request_json, step, ex.seq)
            return _Answer(status, body, headers)
        if step.action is MockAction.STREAM_FAIL_AFTER and not ex.stream:
            status, headers, body = payloads.stream_failure(request_json, step, ex.seq)
            return _Answer(status, body, headers)
        if step.action is MockAction.DELAY:
            seconds = step.params.get("seconds", 1.0)
            if isinstance(seconds, bool) or not isinstance(seconds, int | float):
                raise ValueError("DELAY step needs a numeric 'seconds'")
            if not 0 <= seconds <= MAX_DELAY_S:
                raise ValueError("DELAY 'seconds' must be between 0 and 600")
            ex.summary["delay_s"] = seconds
            await asyncio.sleep(seconds)

        if step.action in _TOOL_ACTIONS:
            tool = payloads.find_function_tool(request_json)
            if tool is None:
                ex.summary["no_tools"] = True
            else:
                ex.summary["tool"] = tool[0]
                ex.summary["tool_style"] = tool[2]
        if ex.stream:
            build_stream = (
                payloads.responses_stream
                if ex.endpoint is Endpoint.RESPONSES
                else payloads.chat_stream
            )
            return _StreamAnswer(build_stream(request_json, step, ex.seq))
        builders = (
            payloads.RESPONSES_BUILDERS
            if ex.endpoint is Endpoint.RESPONSES
            else payloads.CHAT_BUILDERS
        )
        status, headers, body = builders[_BUILDER_FOR_ACTION[step.action]](
            request_json, step, ex.seq
        )
        ex.summary.update(_summarize(ex.endpoint, body))
        return _Answer(status, body, headers)


def create_mock_app(
    script: FaultScript,
    recorder: TrafficRecorder,
    *,
    base_path: str = DEFAULT_BASE_PATH,
    fake_key: str | None = None,
    max_requests: int = DEFAULT_MAX_REQUESTS,
) -> FastAPI:
    """Build the mock. Routes: ``GET {base}/models``, ``POST {base}/responses``,
    ``POST {base}/chat/completions``; anything else is a recorded 404/405."""
    service = _MockService(script, recorder, fake_key, max_requests)
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""
    app = FastAPI(title="FlightCheck mock", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get(f"{base}/models")
    async def list_models(request: Request) -> Response:
        return await service.handle(request, Endpoint.MODELS)

    @app.post(f"{base}/responses")
    async def responses(request: Request) -> Response:
        return await service.handle(request, Endpoint.RESPONSES)

    @app.post(f"{base}/chat/completions")
    async def chat_completions(request: Request) -> Response:
        return await service.handle(request, Endpoint.CHAT_COMPLETIONS)

    @app.exception_handler(StarletteHTTPException)
    async def unknown_route(request: Request, exc: StarletteHTTPException) -> Response:
        status = exc.status_code if exc.status_code in (404, 405) else 404
        return await service.handle(request, None, status)

    return app
