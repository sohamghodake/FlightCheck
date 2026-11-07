"""FaultScript: ordered steps, default fallback, thread safety (plan §18.2)."""

import threading

import pytest
from pydantic import ValidationError

from flightcheck.mock.faults import FaultScript, MockAction, MockStep


def test_actions_match_plan() -> None:
    assert [a.value for a in MockAction] == [
        "ok_text",
        "tool_call",
        "truncate",
        "truncate_tool_call",
        "http_error",
        "stream_fail_after",
        "keepalive_stream",
        "delay",
    ]


def test_steps_in_order_then_default() -> None:
    script = FaultScript(
        steps=[
            MockStep(id="a", action=MockAction.HTTP_ERROR, params={"status": 503}),
            MockStep(id="b", action=MockAction.OK_TEXT),
        ]
    )
    assert [script.next_step().id for _ in range(4)] == ["a", "b", "default", "default"]
    assert script.consumed == 2
    assert script.default_step.action is MockAction.OK_TEXT


def test_custom_default_and_reset() -> None:
    script = FaultScript(
        steps=[MockStep(id="a", action=MockAction.OK_TEXT)],
        default_step=MockStep(id="busy", action=MockAction.HTTP_ERROR),
    )
    assert script.next_step().id == "a"
    assert script.next_step().id == "busy"
    script.reset()
    assert script.consumed == 0
    assert script.next_step().id == "a"


def test_empty_script_serves_default() -> None:
    assert FaultScript().next_step().id == "default"


def test_scripts_do_not_share_state() -> None:
    one = FaultScript(steps=[MockStep(id="a", action=MockAction.OK_TEXT)])
    two = FaultScript(steps=[MockStep(id="a", action=MockAction.OK_TEXT)])
    one.next_step()
    assert two.consumed == 0


def test_validation_from_plain_data() -> None:
    script = FaultScript.model_validate(
        {"steps": [{"id": "t", "action": "tool_call", "params": {"scenario": "S02"}}]}
    )
    assert script.steps[0].action is MockAction.TOOL_CALL
    with pytest.raises(ValidationError):
        MockStep(id="x", action="explode")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        MockStep.model_validate({"id": "x", "action": "ok_text", "extra": 1})


def test_next_step_is_thread_safe() -> None:
    n = 400
    script = FaultScript(steps=[MockStep(id=str(i), action=MockAction.OK_TEXT) for i in range(n)])
    seen: list[str] = []
    seen_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        local = [script.next_step().id for _ in range(n // 8 + 10)]
        with seen_lock:
            seen.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    scripted = [s for s in seen if s != "default"]
    assert sorted(scripted, key=int) == [str(i) for i in range(n)]  # each step exactly once
    assert seen.count("default") == 8 * (n // 8 + 10) - n
    assert script.consumed == n
