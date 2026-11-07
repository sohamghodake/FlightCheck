"""SSE streaming: byte sequences, event shapes, [DONE] handling, recording, disconnects (5B)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionChunk
from openai.types.responses import Response, ResponseStreamEvent
from pydantic import TypeAdapter

from flightcheck.mock import (
    FaultScript,
    MockAction,
    MockStep,
    TrafficRecorder,
    create_mock_app,
    payloads,
)

FAKE = "fc-fake-key-0001"
AUTH = {"Authorization": f"Bearer {FAKE}"}
BASE = "/api/model/v1"
LIVE_KEY = "bc_live_" + "Str3amPl4ntedValue"
SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
    "required": ["city", "days"],
}
RESPONSES_TOOL = {"type": "function", "name": "get_weather", "parameters": SCHEMA}
CHAT_TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": SCHEMA}}
ARGS = '{"city":"fc_value","days":1}'
EVENT_ADAPTER: TypeAdapter[ResponseStreamEvent] = TypeAdapter(ResponseStreamEvent)


@dataclass
class SSE:
    event: str | None
    data: str | None
    comment: str | None = None


def parse_sse(raw: bytes) -> list[SSE]:
    """Minimal SSE parser (spec subset): blank-line dispatch, ``event:``, ``data:``, comments."""
    events: list[SSE] = []
    event: str | None = None
    data: list[str] = []
    text = raw.decode("utf-8")
    assert text.endswith("\n\n") or text == ""
    for line in text.split("\n"):
        if line == "":
            if event is not None or data:
                events.append(SSE(event, "\n".join(data)))
            event, data = None, []
        elif line.startswith(":"):
            events.append(SSE(None, None, line[1:].strip()))
        else:
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
    assert event is None and not data, "unterminated event"
    return events


def model_events(events: list[SSE]) -> list[SSE]:
    return [e for e in events if e.comment is None]


def s(action: MockAction, **params: object) -> MockStep:
    return MockStep(id=f"step_{action.value}", action=action, params={"scenario": "S7", **params})


def rreq(**extra: object) -> dict:
    return {"model": "qwen-3.8-27b", "input": "hi", "store": False, "stream": True, **extra}


def creq(**extra: object) -> dict:
    return {
        "model": "qwen-3.8-27b",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        **extra,
    }


def responses_events(raw: bytes) -> list[ResponseStreamEvent]:
    parsed = []
    for e in model_events(parse_sse(raw)):
        assert e.data is not None
        payload = json.loads(e.data)
        assert payload["type"] == e.event  # the SSE event name matches the payload type
        parsed.append(EVENT_ADAPTER.validate_python(payload))
    assert [p.sequence_number for p in parsed] == list(range(len(parsed)))
    return parsed


def chat_chunks(raw: bytes) -> tuple[list[ChatCompletionChunk], bool]:
    """Parsed chunks and whether ``[DONE]`` was the last event."""
    events = model_events(parse_sse(raw))
    done = bool(events) and events[-1].data == "[DONE]"
    assert all(e.event is None for e in events)
    assert sum(e.data == "[DONE]" for e in events) == int(done)
    chunks = [
        ChatCompletionChunk.model_validate(json.loads(e.data or ""))
        for e in events
        if e.data != "[DONE]"
    ]
    return chunks, done


@pytest.fixture
def make():  # type: ignore[no-untyped-def]
    def _make(*steps: MockStep, **kwargs: object) -> tuple[TestClient, TrafficRecorder]:
        recorder = TrafficRecorder()
        kwargs.setdefault("fake_key", FAKE)
        app = create_mock_app(FaultScript(steps=list(steps)), recorder, **kwargs)  # type: ignore[arg-type]
        return TestClient(app, base_url="http://127.0.0.1"), recorder

    return _make


# --- pure builders --------------------------------------------------------------------------


def test_text_pieces_and_argument_pieces_concatenate_back() -> None:
    for text in ("FlightCheck mock answer [S1]", "  lead and trail  ", "one", ""):
        assert "".join(payloads.text_pieces(text)) == text
    assert payloads.text_pieces("a b") == ["a ", "b"]
    assert "".join(payloads.argument_pieces(ARGS)) == ARGS
    assert all(len(p) <= 8 for p in payloads.argument_pieces(ARGS))


def test_sse_framing() -> None:
    assert payloads.sse_event("response.created", {"a": 1}) == (
        b'event: response.created\ndata: {"a":1}\n\n'
    )
    assert payloads.sse_data({"a": 1}) == b'data: {"a":1}\n\n'
    assert payloads.KEEPALIVE == b": keepalive\n\n"
    assert payloads.DONE == b"data: [DONE]\n\n"


@pytest.mark.parametrize("bad", [-1, "2", True, 1.5])
def test_fail_after_rejects_bad_n(bad: object) -> None:
    with pytest.raises(ValueError):
        payloads.responses_stream(rreq(), s(MockAction.STREAM_FAIL_AFTER, n=bad), 1)


def test_responses_text_stream_sequence() -> None:
    st = payloads.responses_stream(rreq(), s(MockAction.OK_TEXT), 3)
    assert st.headers == {"x-request-id": "fc_mock_3", "cache-control": "no-cache"}
    events = responses_events(b"".join(st.chunks))
    assert [e.type for e in events] == [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.delta",
        "response.output_text.delta",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.completed",
    ]
    deltas = "".join(e.delta for e in events if e.type == "response.output_text.delta")
    assert deltas == "FlightCheck mock answer [S7]"
    created, final = events[0], events[-1]
    assert created.type == "response.created" and created.response.status == "in_progress"
    assert final.type == "response.completed"
    assert final.response.output_text == deltas and final.response.id == "resp_fc_mock_3"
    assert st.outcome == {
        "finish": "completed",
        "call_ids": [],
        "terminal_event": "response.completed",
    }


def test_responses_stream_final_matches_non_stream_body() -> None:
    st = payloads.responses_stream(rreq(tools=[RESPONSES_TOOL]), s(MockAction.TOOL_CALL), 2)
    _, _, body = payloads.responses_tool_call(
        rreq(tools=[RESPONSES_TOOL]), s(MockAction.TOOL_CALL), 2
    )
    final = json.loads(model_events(parse_sse(st.chunks[-1]))[0].data or "")
    assert final["response"] == body


def test_responses_tool_call_stream() -> None:
    st = payloads.responses_stream(rreq(tools=[RESPONSES_TOOL]), s(MockAction.TOOL_CALL), 2)
    events = responses_events(b"".join(st.chunks))
    types = [e.type for e in events]
    assert types[:3] == ["response.created", "response.in_progress", "response.output_item.added"]
    assert types[-3:] == [
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
    ]
    added = events[2]
    assert added.type == "response.output_item.added"
    assert added.item.type == "function_call" and added.item.call_id == "call_fc_S7_2"
    deltas = "".join(e.delta for e in events if e.type == "response.function_call_arguments.delta")
    assert deltas == ARGS
    assert st.outcome["finish"] == "tool_call" and st.outcome["call_ids"] == ["call_fc_S7_2"]


def test_responses_truncated_stream() -> None:
    st = payloads.responses_stream(rreq(), s(MockAction.TRUNCATE), 1)
    events = responses_events(b"".join(st.chunks))
    final = events[-1]
    assert final.type == "response.incomplete"
    assert final.response.status == "incomplete"
    assert final.response.incomplete_details is not None
    assert final.response.incomplete_details.reason == "max_output_tokens"
    assert final.response.output_text == "FlightCheck partial [S7]"
    assert st.outcome["finish"] == "truncated"
    assert st.outcome["terminal_event"] == "response.incomplete"


def test_responses_truncated_tool_call_stream_never_completes_arguments() -> None:
    st = payloads.responses_stream(
        rreq(tools=[RESPONSES_TOOL]), s(MockAction.TRUNCATE_TOOL_CALL), 1
    )
    events = responses_events(b"".join(st.chunks))
    types = [e.type for e in events]
    assert "response.function_call_arguments.done" not in types
    assert types[-1] == "response.incomplete"
    partial = "".join(e.delta for e in events if e.type == "response.function_call_arguments.delta")
    with pytest.raises(json.JSONDecodeError):
        json.loads(partial)
    assert st.outcome["finish"] == "truncated_tool_call"


@pytest.mark.parametrize("n", [0, 2, 99])
def test_responses_stream_fail_after(n: int) -> None:
    st = payloads.responses_stream(rreq(), s(MockAction.STREAM_FAIL_AFTER, n=n), 1)
    events = responses_events(b"".join(st.chunks))
    deltas = [e for e in events if e.type == "response.output_text.delta"]
    assert len(deltas) == min(n, 4)  # the answer has 4 word pieces
    final = events[-1]
    assert final.type == "response.failed"
    assert final.response.status == "failed"
    assert final.response.error is not None and final.response.error.code == "server_error"
    assert not any(e.type in ("response.completed", "response.incomplete") for e in events)
    assert st.outcome == {"finish": "failed", "call_ids": [], "deltas": min(n, 4)}


def test_responses_keepalive_stream() -> None:
    plain = payloads.responses_stream(rreq(), s(MockAction.OK_TEXT), 1)
    st = payloads.responses_stream(rreq(), s(MockAction.KEEPALIVE_STREAM), 1)
    parsed = parse_sse(b"".join(st.chunks))
    comments = [e for e in parsed if e.comment is not None]
    assert comments and all(c.comment == "keepalive" for c in comments)
    assert len(comments) == len(plain.chunks) == st.outcome["keepalives"]
    # keepalives are not model output: the model events are exactly the plain stream's
    assert model_events(parsed) == model_events(parse_sse(b"".join(plain.chunks)))
    assert parsed[0].comment == "keepalive"


def test_chat_text_stream() -> None:
    st = payloads.chat_stream(creq(), s(MockAction.OK_TEXT), 4)
    chunks, done = chat_chunks(b"".join(st.chunks))
    assert done
    assert chunks[0].choices[0].delta.role == "assistant"
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text == "FlightCheck mock answer [S7]"
    assert [c.choices[0].finish_reason for c in chunks][-1] == "stop"
    assert all(c.choices[0].finish_reason is None for c in chunks[:-1])
    assert {c.id for c in chunks} == {"chatcmpl-fc_mock_4"}
    assert all(c.object == "chat.completion.chunk" for c in chunks)
    assert st.outcome == {
        "finish": "completed",
        "call_ids": [],
        "finish_reason": "stop",
        "done_sent": True,
    }


@pytest.mark.parametrize("tool", [CHAT_TOOL, RESPONSES_TOOL])
def test_chat_tool_call_stream(tool: dict) -> None:
    st = payloads.chat_stream(creq(tools=[tool]), s(MockAction.TOOL_CALL), 2)
    chunks, done = chat_chunks(b"".join(st.chunks))
    assert done
    first = (chunks[0].choices[0].delta.tool_calls or [])[0]
    assert first.id == "call_fc_S7_2" and first.function is not None
    assert first.function.name == "get_weather"
    args = "".join(
        (c.choices[0].delta.tool_calls or [])[0].function.arguments or ""  # type: ignore[union-attr]
        for c in chunks
        if c.choices[0].delta.tool_calls
    )
    assert args == ARGS
    assert chunks[-1].choices[0].finish_reason == "tool_calls"
    assert st.outcome["finish"] == "tool_call" and st.outcome["call_ids"] == ["call_fc_S7_2"]


def test_chat_truncated_streams() -> None:
    text, done = chat_chunks(
        b"".join(payloads.chat_stream(creq(), s(MockAction.TRUNCATE), 1).chunks)
    )
    assert done and text[-1].choices[0].finish_reason == "length"
    st = payloads.chat_stream(creq(tools=[CHAT_TOOL]), s(MockAction.TRUNCATE_TOOL_CALL), 1)
    tool, done = chat_chunks(b"".join(st.chunks))
    assert done and tool[-1].choices[0].finish_reason == "length"
    assert st.outcome["finish"] == "truncated_tool_call"


def test_chat_stream_fail_after_has_no_done_and_no_finish() -> None:
    st = payloads.chat_stream(creq(), s(MockAction.STREAM_FAIL_AFTER, n=2), 1)
    assert payloads.DONE not in st.chunks
    chunks, done = chat_chunks(b"".join(st.chunks))
    assert not done
    assert all(c.choices[0].finish_reason is None for c in chunks)
    assert "".join(c.choices[0].delta.content or "" for c in chunks) == "FlightCheck mock "
    assert st.outcome == {
        "finish": "eof_without_done",
        "call_ids": [],
        "deltas": 2,
        "done_sent": False,
    }


def test_chat_keepalive_stream() -> None:
    st = payloads.chat_stream(creq(), s(MockAction.KEEPALIVE_STREAM), 1)
    parsed = parse_sse(b"".join(st.chunks))
    assert sum(e.comment == "keepalive" for e in parsed) == st.outcome["keepalives"] > 0
    _, done = chat_chunks(b"".join(st.chunks))
    assert done


def test_streams_are_deterministic() -> None:
    for action in (MockAction.OK_TEXT, MockAction.TOOL_CALL, MockAction.KEEPALIVE_STREAM):
        for build in (payloads.responses_stream, payloads.chat_stream):
            body = rreq(tools=[RESPONSES_TOOL])
            assert build(body, s(action), 1) == build(body, s(action), 1)


# --- through the app ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "finish"),
    [
        (MockAction.OK_TEXT, "completed"),
        (MockAction.TOOL_CALL, "tool_call"),
        (MockAction.TRUNCATE, "truncated"),
        (MockAction.TRUNCATE_TOOL_CALL, "truncated_tool_call"),
        (MockAction.STREAM_FAIL_AFTER, "failed"),
        (MockAction.KEEPALIVE_STREAM, "completed"),
        (MockAction.DELAY, "completed"),
    ],
)
def test_app_responses_stream_every_action(make, action: MockAction, finish: str) -> None:  # type: ignore[no-untyped-def]
    params = {"seconds": 0} if action is MockAction.DELAY else {}
    client, rec = make(s(action, **params))
    resp = client.post(f"{BASE}/responses", json=rreq(tools=[RESPONSES_TOOL]), headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-request-id"] == "fc_mock_1"
    events = responses_events(resp.content)
    assert events[-1].type in ("response.completed", "response.incomplete", "response.failed")
    (ex,) = rec.list()
    assert ex.stream is True and ex.response_status == 200 and ex.step_id == f"step_{action.value}"
    summary = ex.response_summary
    assert summary["finish"] == finish
    assert summary["chunks_sent"] == summary["chunks_planned"] > 0
    assert summary["action"] == action.value and summary["auth"] == "fake_key"


@pytest.mark.parametrize(
    ("action", "finish", "done"),
    [
        (MockAction.OK_TEXT, "completed", True),
        (MockAction.TOOL_CALL, "tool_call", True),
        (MockAction.TRUNCATE, "truncated", True),
        (MockAction.TRUNCATE_TOOL_CALL, "truncated_tool_call", True),
        (MockAction.STREAM_FAIL_AFTER, "eof_without_done", False),
        (MockAction.KEEPALIVE_STREAM, "completed", True),
        (MockAction.DELAY, "completed", True),
    ],
)
def test_app_chat_stream_every_action(make, action: MockAction, finish: str, done: bool) -> None:  # type: ignore[no-untyped-def]
    params = {"seconds": 0} if action is MockAction.DELAY else {}
    client, rec = make(s(action, **params))
    resp = client.post(f"{BASE}/chat/completions", json=creq(tools=[CHAT_TOOL]), headers=AUTH)
    assert resp.status_code == 200
    _, got_done = chat_chunks(resp.content)
    assert got_done is done
    summary = rec.list()[0].response_summary
    assert summary["finish"] == finish and summary["done_sent"] is done


@pytest.mark.parametrize("path", ["responses", "chat/completions"])
def test_http_error_on_stream_request_is_json(make, path: str) -> None:  # type: ignore[no-untyped-def]
    client, rec = make(s(MockAction.HTTP_ERROR, status=503))
    body = rreq() if path == "responses" else creq()
    resp = client.post(f"{BASE}/{path}", json=body, headers=AUTH)
    assert resp.status_code == 503
    assert resp.headers["content-type"] == "application/json"
    assert resp.json()["error"]["type"] == "server_error"
    ex = rec.list()[0]
    assert ex.stream is True and ex.response_summary["finish"] == "error"


def test_stream_without_tools_records_no_tools(make) -> None:  # type: ignore[no-untyped-def]
    client, rec = make(s(MockAction.TOOL_CALL))
    resp = client.post(f"{BASE}/responses", json=rreq(), headers=AUTH)
    assert responses_events(resp.content)[-1].type == "response.completed"
    summary = rec.list()[0].response_summary
    assert summary["no_tools"] is True and summary["finish"] == "completed"


def test_documented_400s_still_apply_to_streams(make) -> None:  # type: ignore[no-untyped-def]
    client, rec = make(s(MockAction.OK_TEXT))
    resp = client.post(f"{BASE}/responses", json=rreq(background=True), headers=AUTH)
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "unsupported_parameter"
    assert rec.list()[0].stream is True
    ok = client.post(f"{BASE}/responses", json=rreq(), headers=AUTH)
    assert ok.status_code == 200 and rec.list()[1].step_id == "step_ok_text"


def test_stream_false_is_json(make) -> None:  # type: ignore[no-untyped-def]
    client, rec = make()
    resp = client.post(f"{BASE}/responses", json=rreq(stream=False), headers=AUTH)
    assert resp.headers["content-type"] == "application/json"
    Response.model_validate(resp.json())
    assert rec.list()[0].stream is False


def test_bad_fail_after_param_is_recorded_500(make) -> None:  # type: ignore[no-untyped-def]
    client, rec = make(s(MockAction.STREAM_FAIL_AFTER, n=-3))
    resp = client.post(f"{BASE}/responses", json=rreq(), headers=AUTH)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "flightcheck_mock_error"
    assert rec.list()[0].response_summary["error"] == "ValueError"


def test_planted_key_absent_from_stream_and_recording(make) -> None:  # type: ignore[no-untyped-def]
    tool = {
        "type": "function",
        "name": "lookup",
        "parameters": {
            "type": "object",
            "properties": {"k": {"enum": [LIVE_KEY]}},
            "required": ["k"],
        },
    }
    client, rec = make(s(MockAction.TOOL_CALL), s(MockAction.TOOL_CALL))
    headers = {"Authorization": f"Bearer {LIVE_KEY}"}
    body = rreq(tools=[tool], input=f"key {LIVE_KEY}", metadata={"api_key": LIVE_KEY})
    r1 = client.post(f"{BASE}/responses", json=body, headers=headers)
    r2 = client.post(
        f"{BASE}/chat/completions", json=creq(tools=[tool], user=LIVE_KEY), headers=headers
    )
    for resp in (r1, r2):
        assert resp.status_code == 200
        assert LIVE_KEY not in resp.text
    recorded = "".join(e.model_dump_json() for e in rec.list())
    assert LIVE_KEY not in recorded and "Str3amPl4ntedValue" not in recorded


# --- client disconnect (driven at the ASGI level, no sockets) -------------------------------


def _scope(path: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"authorization", f"Bearer {FAKE}".encode()),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 80),
    }


@pytest.mark.parametrize("path", ["responses", "chat/completions"])
def test_client_disconnect_mid_stream_is_recorded(path: str) -> None:
    long_text = " ".join(f"w{i}" for i in range(300))
    recorder = TrafficRecorder()
    app = create_mock_app(
        FaultScript(steps=[s(MockAction.OK_TEXT, text=long_text)]), recorder, fake_key=FAKE
    )
    body = json.dumps(rreq() if path == "responses" else creq()).encode()
    sent: list[dict] = []

    async def run() -> None:
        first_chunk = asyncio.Event()
        calls = 0

        async def receive() -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"type": "http.request", "body": body, "more_body": False}
            await first_chunk.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                first_chunk.set()

        await asyncio.wait_for(app(_scope(f"{BASE}/{path}"), receive, send), timeout=5)

    asyncio.run(run())
    (ex,) = recorder.list()
    summary = ex.response_summary
    assert summary["finish"] == "client_disconnected"
    assert summary["planned_finish"] == "completed"
    assert 1 <= summary["chunks_sent"] < summary["chunks_planned"]
    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert not any(m.get("more_body") is False for m in bodies)  # the stream never finished


def test_send_failure_mid_stream_is_recorded_once() -> None:
    """ASGI 2.4 style: the server reports a gone client by raising OSError from send()."""
    recorder = TrafficRecorder()
    app = create_mock_app(FaultScript(), recorder, fake_key=FAKE)
    body = json.dumps(rreq()).encode()
    scope = _scope(f"{BASE}/responses") | {"asgi": {"version": "3.0", "spec_version": "2.4"}}

    async def run() -> None:
        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        bodies = 0

        async def send(message: dict) -> None:
            nonlocal bodies
            if message["type"] == "http.response.body":
                bodies += 1
                if bodies == 3:
                    raise OSError("client gone")

        try:
            await app(scope, receive, send)
        except Exception:  # noqa: BLE001 - Starlette surfaces it as ClientDisconnect
            pass

    asyncio.run(run())
    (ex,) = recorder.list()
    assert ex.response_summary["finish"] == "client_disconnected"
    assert ex.response_summary["chunks_sent"] == 2
