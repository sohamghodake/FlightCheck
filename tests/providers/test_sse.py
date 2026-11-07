"""SSE parser and stream mappings (plan §14.6), including every stream the mock produces."""

from __future__ import annotations

import json

import pytest

from flightcheck.mock import payloads
from flightcheck.mock.faults import MockAction, MockStep
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


def messages(text: str, chunk: int | None = None) -> list[SSEMessage]:
    pieces = [text] if chunk is None else [text[i : i + chunk] for i in range(0, len(text), chunk)]
    return list(parse_sse(iter_sse_lines(pieces)))


def r_event(name: str, **data: object) -> str:
    return f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n"


def c_chunk(delta: dict, finish: str | None = None, **extra: object) -> str:
    body = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **extra,
    }
    return f"data: {json.dumps(body)}\n\n"


# --- Line splitting -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chunks", "lines"),
    [
        (["a\nb\n"], ["a", "b"]),
        (["a\r\nb\r\n"], ["a", "b"]),
        (["a\rb\r"], ["a", "b"]),
        (["a\r", "\nb\n"], ["a", "b"]),  # CRLF split across chunks is one line ending
        (["a\r", "", "\nb"], ["a", "b"]),
        (["ab", "c", "d\n\n"], ["abcd", ""]),
        (["tail without newline"], ["tail without newline"]),
        (["\n\n"], ["", ""]),
        ([], []),
    ],
)
def test_iter_sse_lines(chunks: list[str], lines: list[str]) -> None:
    assert list(iter_sse_lines(chunks)) == lines


def test_iter_sse_lines_keeps_unicode_line_separators_inside_lines() -> None:
    # str.splitlines (and so httpx2's iter_lines) would split these; SSE does not.
    # U+2028 and U+0085 are legal unescaped inside JSON strings.
    text = 'data: {"t": "a\u2028b\u0085c"}\n\n'
    assert list(iter_sse_lines([text])) == ['data: {"t": "a\u2028b\u0085c"}', ""]
    [message] = messages(text)
    assert json.loads(message.data)["t"] == "a\u2028b\u0085c"
    assert list(iter_sse_lines(["x\x1cy\x0bz\n"])) == ["x\x1cy\x0bz"]


# --- Parsing --------------------------------------------------------------------------------


def test_event_and_data_fields() -> None:
    assert messages("event: ping\ndata: {}\n\n") == [SSEMessage(event="ping", data="{}")]


def test_multi_line_data_joins_with_newlines() -> None:
    [message] = messages("data: line one\ndata:line two\ndata:  indented\n\n")
    assert message.data == "line one\nline two\n indented"


def test_comments_are_dispatched_at_once_with_any_text() -> None:
    parsed = messages(": keepalive\n:\n: queue position 3\ndata: x\n\n")
    assert [m.comment for m in parsed[:3]] == ["keepalive", "", "queue position 3"]
    assert all(m.data == "" for m in parsed[:3])
    assert parsed[3] == SSEMessage(data="x")


def test_blank_line_without_data_dispatches_nothing() -> None:
    assert messages("event: lonely\n\n\n\ndata: y\n\n") == [SSEMessage(data="y")]


def test_event_name_resets_after_dispatch() -> None:
    parsed = messages("event: a\ndata: 1\n\ndata: 2\n\n")
    assert parsed == [SSEMessage(event="a", data="1"), SSEMessage(event=None, data="2")]


def test_id_retry_and_unknown_fields_are_ignored() -> None:
    assert messages("id: 7\nretry: 100\nfoo: bar\nnocolon\ndata: z\n\n") == [SSEMessage(data="z")]


def test_unterminated_event_at_eof_is_discarded() -> None:
    assert messages("data: complete\n\ndata: cut off") == [SSEMessage(data="complete")]


def test_bom_at_stream_start_is_ignored() -> None:
    assert messages("\ufeffdata: x\n\n") == [SSEMessage(data="x")]


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64])
def test_chunk_borders_do_not_matter(chunk: int) -> None:
    text = ': ka\r\nevent: e\r\ndata: {"a": 1}\r\n\r\ndata: [DONE]\r\n\r\n'
    assert messages(text, chunk) == messages(text)
    assert len(messages(text)) == 3


# --- Responses mapping ----------------------------------------------------------------------


def r_kinds(text: str) -> list[str]:
    return [event.kind for event in responses_events(messages(text))]


def test_responses_text_and_completed() -> None:
    text = (
        r_event("response.created", response={})
        + r_event("response.output_text.delta", delta="Hel")
        + r_event("response.output_text.delta", delta="lo")
        + r_event("response.completed", response={"status": "completed"})
    )
    events = list(responses_events(messages(text)))
    assert [e.kind for e in events] == ["unknown", "text_delta", "text_delta", "completed"]
    assert "".join(e.text for e in events) == "Hello"
    assert events[-1].raw_event == "response.completed"
    assert events[-1].data["response"] == {"status": "completed"}


def test_responses_incomplete_keeps_partial_text() -> None:
    text = r_event("response.output_text.delta", delta="part") + r_event(
        "response.incomplete", response={"status": "incomplete"}
    )
    events = list(responses_events(messages(text)))
    assert [e.kind for e in events] == ["text_delta", "incomplete"]
    assert events[0].text == "part"


def test_responses_failed_is_terminal() -> None:
    text = r_event("response.failed", response={"error": {"code": "server_error"}})
    assert r_kinds(text + r_event("response.output_text.delta", delta="late")) == ["failed"]


def test_responses_stops_after_the_first_terminal_event() -> None:
    text = r_event("response.completed", response={}) + r_event("response.failed", response={})
    assert r_kinds(text) == ["completed"]


def test_responses_function_call_delta_and_other_events() -> None:
    text = (
        r_event("response.function_call_arguments.delta", delta='{"ci')
        + r_event("response.function_call_arguments.done", arguments="{}")
        + r_event("response.completed", response={})
    )
    events = list(responses_events(messages(text)))
    assert [e.kind for e in events] == ["tool_call_delta", "unknown", "completed"]
    assert events[0].text == "" and events[0].data["delta"] == '{"ci'


def test_responses_event_name_from_type_when_no_event_field() -> None:
    body = json.dumps({"type": "response.output_text.delta", "delta": "x"})
    assert r_kinds(f"data: {body}\n\n") == ["text_delta", "failed"]


def test_responses_keepalives_never_add_text() -> None:
    text = ": keepalive\n\n" + r_event("response.completed", response={})
    events = list(responses_events(messages(text)))
    assert events[0].kind == "keepalive" and events[0].text == ""
    assert events[0].data == {"comment": "keepalive"}


def test_responses_eof_without_terminal_event_is_failed() -> None:
    events = list(responses_events(messages(r_event("response.output_text.delta", delta="a"))))
    assert [e.kind for e in events] == ["text_delta", "failed"]
    assert events[-1].data == {"reason": EOF_REASON} and events[-1].raw_event is None


def test_responses_keepalives_only_then_eof_is_failed() -> None:
    events = list(responses_events(messages(": keepalive\n\n: keepalive\n\n")))
    assert [e.kind for e in events] == ["keepalive", "keepalive", "failed"]
    assert events[-1].data["reason"] == EOF_REASON


def test_responses_empty_stream_is_failed() -> None:
    assert r_kinds("") == ["failed"]


@pytest.mark.parametrize(
    "text",
    [
        "event: response.output_text.delta\ndata: {not json\n\n",
        "event: response.completed\ndata: [1, 2]\n\n",
        r_event("response.output_text.delta", delta=5),
    ],
)
def test_responses_malformed_event_data_raises(text: str) -> None:
    with pytest.raises(SSEProtocolError):
        list(responses_events(messages(text)))


# --- Chat mapping ---------------------------------------------------------------------------


def c_events(text: str) -> list:
    return list(chat_events(messages(text)))


def test_chat_text_then_stop_then_done() -> None:
    text = (
        c_chunk({"role": "assistant", "content": ""})
        + c_chunk({"content": "Hi "})
        + c_chunk({"content": "there"})
        + c_chunk({}, "stop")
        + "data: [DONE]\n\n"
    )
    events = c_events(text)
    assert [e.kind for e in events] == ["unknown", "text_delta", "text_delta", "completed"]
    assert "".join(e.text for e in events) == "Hi there"
    assert events[-1].raw_event == "chat.completion.chunk"


@pytest.mark.parametrize(
    ("finish", "kind"),
    [("stop", "completed"), ("tool_calls", "completed"), ("length", "incomplete")],
)
def test_chat_finish_reasons(finish: str, kind: str) -> None:
    assert [e.kind for e in c_events(c_chunk({}, finish))] == [kind]


def test_chat_content_in_the_finish_chunk_is_kept() -> None:
    events = c_events(c_chunk({"content": "last"}, "length"))
    assert [(e.kind, e.text) for e in events] == [("text_delta", "last"), ("incomplete", "")]


def test_chat_tool_call_deltas() -> None:
    call = {"index": 0, "id": "call_1", "function": {"name": "f", "arguments": ""}}
    text = (
        c_chunk({"tool_calls": [call]})
        + c_chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]})
        + c_chunk({}, "tool_calls")
        + "data: [DONE]\n\n"
    )
    assert [e.kind for e in c_events(text)] == ["tool_call_delta", "tool_call_delta", "completed"]


def test_chat_done_without_finish_reason_is_failed() -> None:
    events = c_events(c_chunk({"content": "a"}) + "data: [DONE]\n\n")
    assert [e.kind for e in events] == ["text_delta", "failed"]
    assert events[-1].data == {"reason": DONE_WITHOUT_FINISH_REASON}


def test_chat_eof_without_done_or_finish_is_failed() -> None:
    events = c_events(c_chunk({"content": "a"}))
    assert [e.kind for e in events] == ["text_delta", "failed"]
    assert events[-1].data == {"reason": EOF_REASON}


def test_chat_unrecognised_finish_reason_is_failed() -> None:
    [event] = c_events(c_chunk({}, "content_filter"))
    assert event.kind == "failed"
    assert event.data["reason"] == UNRECOGNISED_FINISH_REASON
    assert event.data["finish_reason"] == "content_filter"


def test_chat_error_chunk_is_failed() -> None:
    body = json.dumps({"error": {"message": "overloaded"}})
    [event] = c_events(f"data: {body}\n\n" + c_chunk({}, "stop"))
    assert event.kind == "failed" and event.data["reason"] == ERROR_CHUNK_REASON


def test_chat_chunk_without_choices_is_unknown() -> None:
    usage = "data: " + json.dumps({"object": "chat.completion.chunk", "choices": []}) + "\n\n"
    assert [e.kind for e in c_events(usage + c_chunk({}, "stop"))] == ["unknown", "completed"]


def test_chat_only_choice_zero_counts() -> None:
    body = {
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 1, "delta": {"content": "other"}, "finish_reason": "stop"},
            {"index": 0, "delta": {"content": "mine"}, "finish_reason": None},
        ],
    }
    events = c_events(f"data: {json.dumps(body)}\n\n")
    assert [(e.kind, e.text) for e in events] == [("text_delta", "mine"), ("failed", "")]


def test_chat_keepalives() -> None:
    events = c_events(": keepalive\n\n" + c_chunk({}, "stop"))
    assert [e.kind for e in events] == ["keepalive", "completed"]


def test_chat_malformed_chunk_raises() -> None:
    with pytest.raises(SSEProtocolError):
        c_events("data: {oops\n\n")


# --- Every stream the mock builds (tests/mock/test_streaming.py) -----------------------------

SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
RESPONSES_TOOL = {"type": "function", "name": "get_weather", "parameters": SCHEMA}
CHAT_TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": SCHEMA}}
STREAMABLE = [
    MockAction.OK_TEXT,
    MockAction.TOOL_CALL,
    MockAction.TRUNCATE,
    MockAction.TRUNCATE_TOOL_CALL,
    MockAction.STREAM_FAIL_AFTER,
    MockAction.KEEPALIVE_STREAM,
]
RESPONSES_TERMINAL = {
    "completed": "completed",
    "tool_call": "completed",
    "truncated": "incomplete",
    "truncated_tool_call": "incomplete",
    "failed": "failed",
}
CHAT_TERMINAL = {**RESPONSES_TERMINAL, "eof_without_done": "failed"}


def mock_stream_text(stream: payloads.SSEStream) -> str:
    return b"".join(stream.chunks).decode("utf-8")


@pytest.mark.parametrize("action", STREAMABLE)
@pytest.mark.parametrize("chunk", [None, 3])
def test_every_mock_responses_stream(action: MockAction, chunk: int | None) -> None:
    request = {"model": "qwen-3.8-27b", "input": "hi", "stream": True, "tools": [RESPONSES_TOOL]}
    stream = payloads.responses_stream(request, MockStep(id="x", action=action), 1)
    events = list(responses_events(messages(mock_stream_text(stream), chunk)))
    assert events[-1].kind == RESPONSES_TERMINAL[stream.outcome["finish"]]
    assert sum(e.terminal for e in events) == 1
    keepalives = sum(e.kind == "keepalive" for e in events)
    assert keepalives == stream.outcome.get("keepalives", 0)
    text = "".join(e.text for e in events)
    if action in (MockAction.TOOL_CALL, MockAction.TRUNCATE_TOOL_CALL):
        assert text == ""
        assert any(e.kind == "tool_call_delta" for e in events)
    else:
        assert "keepalive" not in text and text  # keepalives are never output


@pytest.mark.parametrize("action", STREAMABLE)
@pytest.mark.parametrize("tool", [CHAT_TOOL, RESPONSES_TOOL])
def test_every_mock_chat_stream(action: MockAction, tool: dict) -> None:
    request = {
        "model": "qwen-3.8-27b",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "tools": [tool],
    }
    stream = payloads.chat_stream(request, MockStep(id="x", action=action), 1)
    events = list(chat_events(messages(mock_stream_text(stream), 5)))
    assert events[-1].kind == CHAT_TERMINAL[stream.outcome["finish"]]
    assert sum(e.terminal for e in events) == 1
    if action is MockAction.STREAM_FAIL_AFTER:
        assert events[-1].data == {"reason": EOF_REASON}
        assert "".join(e.text for e in events) == "FlightCheck mock "


def test_mock_keepalive_only_then_eof() -> None:
    # The mock never sends this by itself; build it from its keepalive bytes.
    text = (payloads.KEEPALIVE * 3).decode("utf-8")
    assert [e.kind for e in responses_events(messages(text))] == ["keepalive"] * 3 + ["failed"]
    assert [e.kind for e in chat_events(messages(text))] == ["keepalive"] * 3 + ["failed"]
