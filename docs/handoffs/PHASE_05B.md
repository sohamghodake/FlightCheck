# Phase 5B handoff — Mock streaming, MockServer, OpenAI-SDK compatibility

- Date / session: 2026-11-07, same Claude Code chat as Phase 5A (5A+5B are paired).
- Status: done (P0). The optional P1 `flightcheck mock-server` CLI command was not built.
- What was built (files, public functions):
  - `mock/payloads.py`: `SSEStream` (headers, chunks, outcome), `responses_stream`,
    `chat_stream` (each `(request_json, step, seq) -> SSEStream`), `sse_event`, `sse_data`,
    `text_pieces`, `argument_pieces`, `fail_after`, `stream_headers`, `KEEPALIVE`, `DONE`.
    Each stream is built from the same non-stream body, so the terminal Responses event carries
    exactly the 5A body.
  - `mock/app.py`: `stream: true` now gets SSE (`text/event-stream`, `x-request-id`,
    `cache-control: no-cache`); `_RecordingStreamingResponse` records each stream once when it
    ends (normal end, disconnect, cancellation). The 5A 501 placeholder is gone.
  - `mock/server.py`: `MockServer(script=None, *, fake_key, max_requests, base_path, recorder,
    startup_timeout_s)` with `start()`, `stop()`, context manager, `.host`, `.port`, `.origin`,
    `.base_url`, `.recorder`; `MockServerError` (a `FlightCheckError`, exit 3).
  - `mock/__init__.py` exports `MockServer`, `MockServerError`.
  - `docs/ARCHITECTURE.md` §8: the payload fields the OpenAI SDK requires.
- Tests added (files, count) and gate results: `tests/mock/test_streaming.py` (46),
  `test_server.py` (8, `slow`), `test_sdk_compat.py` (16, `slow`); `test_app_nonstream.py`
  lost its two 501 tests (78 left). 612 total (+68) at the 5B commit; 625 after the follow-up fixes. `ruff check .`, `ruff format --check .`,
  `pytest -m "not live" -q`, `python scripts/eval_fixtures.py --check` (107/107, 0, 0) green on
  Python 3.12.14; the mock suite also passes with unraisable-exception warnings as errors.
  Server/SDK tests ran 3× in a row without flakes.
- Decisions made (ADR ids): ADR-015 (stream shapes, fail/keepalive semantics, recording,
  MockServer).
- Deviations from the plan and why:
  - Responses streams also send `response.in_progress`, `output_item.added`/`.done`,
    `content_part.added`/`.done` and `output_text.done` (OpenAI's sequence). The plan names only
    created → delta → terminal, but the SDK's `responses.stream()` helper fails without the
    `added` events. All [UNVERIFIED].
  - Chat `STREAM_FAIL_AFTER` ends the body cleanly instead of resetting the TCP connection:
    deterministic, and an SSE reader sees the same "EOF without [DONE]". A real abort would need
    an exception in the ASGI app (Uvicorn would log a traceback).
  - `MockServer` binds the socket itself and hands it to Uvicorn (no free-port race), instead of
    passing `host`/`port` only.
  - `test_app_nonstream.py` (5A file): replaced the two 501 tests and the 501 in the
    request-id test with SSE expectations (necessary: the placeholder was removed).
- Findings while implementing:
  - Uvicorn 0.54 `Server.capture_signals` returns early off the main thread, so no signal
    handling has to be disabled. It reports ASGI `spec_version` 2.3, so Starlette's
    disconnect listener path is used (both 2.3 and 2.4 paths are tested at ASGI level).
  - Race: a stream is recorded right after its last byte, so a fast client could read the
    recorder before the exchange appeared (first SDK test run failed on this). Rule: read the
    recorder after `MockServer.stop()`, which waits for in-flight requests. Documented in the
    `MockServer` docstring and ADR-015.
  - Self-review fix: a disconnect during the final `sleep(0)` would have labelled a complete
    stream `client_disconnected`; completion is now "every chunk reached the server".
  - The SDK raises `APIError` on any SSE payload with a top-level `error` key; the mock never
    sends one (`response.failed` nests the error under `response`).
  - httpx2 rejects a whitespace-only `Authorization` header client-side, so the SDK cannot send
    a blank key at all.
- Follow-up fixes after the 5B commit (same phase, separate commit):
  - Slow streams: `interval_s` step parameter (pause between SSE chunks; ADR-015 item 8).
    Tests: `test_streaming.py` interval tests (validation, exact sleeps, real elapsed time).
  - 5A issue: a failure inside `TrafficRecorder.record` is now caught in `mock/app.py`; the
    client still gets its answer and `x-request-id`; `TrafficRecorder.record_failures`,
    `note_record_failure()` and `complete` added (ADR-015 item 9). Tests:
    `test_recorder_failure_still_answers`, `test_recorder_failure_after_a_stream_keeps_the_stream`,
    `test_record_failures_make_the_recording_incomplete`.
  - 5A issue: `test_delay_really_waits` now times the delayed request itself (≥ 0.2 s) and
    compares `at_ms` with an undelayed request.
  - 5A issue: fastapi/uvicorn/httpx2 re-checked on PyPI; unchanged (ADR-008 re-check note).
  - The new tests were mutation-checked: removing the interval sleep or the recorder guard
    makes them fail.
- Out-of-scope changes (file, reason, test): none outside `mock/`, `tests/mock/` and the ADR-008
  re-check note.
- Known issues / limitations:
  - All stream payload fields, the Chat stream format and the keepalive text are [UNVERIFIED]
    until the Phase 10 probe.
  - `RecordedExchange` order in `TrafficRecorder.list()` is append order; a stream is appended
    when it ends, so it can follow a later request. Sort by `seq` for arrival order.
  - Phase 7 should treat `TrafficRecorder.complete is False` as `needs_review` for traffic
    checks (some requests are missing from the recording).
  - Tests that start the SDK set `NO_PROXY` and clear proxy variables; the Phase 7 harness must
    do the same for the app's child process.
- Exact next step for the next phase: Phase 6 (provider adapters and capability registry). Use
  `MockServer` as the adapter test double (`with MockServer(script, fake_key=...) as s:` then
  point the adapter at `s.base_url`; read `s.recorder` after the block). The adapter's SSE parser
  (plan §14.6) should handle every byte sequence in `tests/mock/test_streaming.py`.
