"""TrafficRecorder: hashing, header redaction, body redaction, thread safety (plan §17.3)."""

import threading

from flightcheck.core.models import Endpoint, RecordedExchange, ScenarioResult
from flightcheck.mock.recorder import (
    FAKE_KEY_MARK,
    TrafficRecorder,
    auth_status,
    bearer_token,
    canonical_sha256,
    redact_headers,
)

LIVE_KEY = "bc_live_" + "Zx9Qw7Er5Ty3Ui1Op"


def record(rec: TrafficRecorder, **overrides: object) -> RecordedExchange:
    kwargs: dict = {
        "seq": rec.next_seq(),
        "method": "POST",
        "path": "/api/model/v1/responses",
        "endpoint": Endpoint.RESPONSES,
        "headers": {"Authorization": "Bearer fc-fake-key", "Content-Type": "application/json"},
        "fake_key": "fc-fake-key",
        "request_json": {"model": "qwen-3.8-27b", "input": "hi"},
        "raw_body": b"",
        "stream": False,
        "step_id": "s1",
        "response_status": 200,
        "response_summary": {"finish": "completed", "call_ids": []},
    }
    kwargs.update(overrides)
    return rec.record(**kwargs)


def test_canonical_hash_is_stable_across_key_order() -> None:
    a = {"model": "m", "input": [{"role": "user", "content": "hi"}], "store": False}
    b = {"store": False, "input": [{"content": "hi", "role": "user"}], "model": "m"}
    assert canonical_sha256(a) == canonical_sha256(b)
    assert len(canonical_sha256(a)) == 64
    assert canonical_sha256(a) != canonical_sha256({**a, "store": True})
    # list order is meaningful
    assert canonical_sha256([1, 2]) != canonical_sha256([2, 1])


def test_bearer_parsing_and_auth_status() -> None:
    assert bearer_token(None) is None
    assert bearer_token("") is None
    assert bearer_token("Bearer ") is None
    assert bearer_token("Bearer    ") is None
    assert bearer_token("Basic abc") is None
    assert bearer_token("bearer  tok ") == "tok"
    assert auth_status(None, "k") == "missing"
    assert auth_status("Bearer k", "k") == "fake_key"
    assert auth_status("Bearer other", "k") == "other_key"
    assert auth_status("Bearer other", None) == "unchecked"


def test_header_redaction() -> None:
    headers = {
        "Authorization": "Bearer fc-fake-key",
        "X-Api-Key": "anything",
        "Cookie": "session=abc",
        "User-Agent": "OpenAI/Python 3.46.0",
        "X-Note": f"key {LIVE_KEY}",
    }
    safe = redact_headers(headers, "fc-fake-key")
    assert safe["authorization"] == FAKE_KEY_MARK
    assert safe["x-api-key"] == "[REDACTED]"
    assert safe["cookie"] == "[REDACTED]"
    assert safe["user-agent"] == "OpenAI/Python 3.46.0"
    assert LIVE_KEY not in safe["x-note"]
    assert redact_headers({"authorization": "Bearer fc-fake-key"}, None) == {
        "authorization": "[REDACTED]"
    }
    assert redact_headers({"authorization": "Bearer real"}, "fc-fake-key") == {
        "authorization": "[REDACTED]"
    }


def test_record_builds_exchange() -> None:
    rec = TrafficRecorder()
    ex = record(rec)
    assert ex.seq == 1 and ex.at_ms >= 0
    assert ex.request_headers["authorization"] == FAKE_KEY_MARK
    assert ex.request_sha256 == canonical_sha256({"model": "qwen-3.8-27b", "input": "hi"})
    assert rec.list() == [ex] and len(rec) == 1
    rec.list().clear()  # a snapshot, not the internal list
    assert len(rec) == 1


def test_invalid_body_is_hashed_from_redacted_raw_text() -> None:
    rec = TrafficRecorder()
    ex = record(rec, request_json=None, raw_body=f"{{not json {LIVE_KEY}".encode())
    assert ex.request_json is None
    assert ex.request_sha256 == canonical_sha256("{not json bc_live_…[REDACTED]")


def test_retries_hash_identically() -> None:
    rec = TrafficRecorder()
    one = record(rec, request_json={"a": 1, "b": [1, 2]})
    two = record(rec, request_json={"b": [1, 2], "a": 1})
    assert one.request_sha256 == two.request_sha256
    assert (one.seq, two.seq) == (1, 2)


def test_planted_live_key_never_recorded() -> None:
    rec = TrafficRecorder()
    record(
        rec,
        headers={"Authorization": f"Bearer {LIVE_KEY}", "X-Debug": LIVE_KEY},
        request_json={
            "model": "qwen-3.8-27b",
            "input": [{"role": "user", "content": f"my key is {LIVE_KEY}"}],
            "metadata": {"api_key": LIVE_KEY},
        },
        response_summary={"note": LIVE_KEY},
    )
    record(rec, request_json=None, raw_body=LIVE_KEY.encode())
    dumped = "".join(e.model_dump_json() for e in rec.list())
    assert LIVE_KEY not in dumped
    assert "Zx9Qw7Er5Ty3Ui1Op" not in dumped
    assert rec.list()[0].request_headers["authorization"] == "[REDACTED]"


def test_max_records_counts_dropped() -> None:
    rec = TrafficRecorder(max_records=2)
    assert rec.complete is True
    for _ in range(5):
        record(rec)
    assert len(rec) == 2 and rec.dropped == 3
    assert rec.complete is False


def test_record_failures_make_the_recording_incomplete() -> None:
    rec = TrafficRecorder()
    record(rec)
    assert rec.complete is True and rec.record_failures == 0
    rec.note_record_failure()
    assert rec.record_failures == 1 and rec.complete is False


def test_concurrent_seq_and_append() -> None:
    rec = TrafficRecorder()

    def worker() -> None:
        for _ in range(50):
            record(rec)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(e.seq for e in rec.list()) == list(range(1, 401))


def test_scenario_result_holds_recorded_exchanges() -> None:
    ex = record(TrafficRecorder())
    result = ScenarioResult.model_validate(
        {
            "scenario_id": "S01",
            "name": "happy path",
            "status": "passed",
            "app_run": None,
            "exchanges": [ex.model_dump(mode="json")],
        }
    )
    assert result.exchanges == [ex]
    assert result.exchanges[0].endpoint is Endpoint.RESPONSES
