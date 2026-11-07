# Phase 5A handoff — Contract-aware mock provider, non-streaming

- Date / session: 2026-11-07, Claude Code chat for Phase 5A.
- Status: done (P0). Streaming and `mock/server.py` are Phase 5B.
- What was built (files, public functions):
  - `mock/faults.py`: `MockAction` (8 actions, §18.2), `MockStep`, `FaultScript` with
    thread-safe `next_step()` (falls back to `default_step`), `consumed`, `reset()`.
  - `mock/payloads.py` (no FastAPI): `responses_text/tool_call/truncated/truncated_tool_call`,
    `chat_text/tool_call/truncated/truncated_tool_call` (each `(request_json, step, seq) ->
    (status, headers, body)`), `http_error`, `stream_failure`, `error_body(status, message,
    code)`, `models_list()`, `find_function_tool` (both tool styles), `arguments_for_schema` /
    `value_for_schema`, `cut_arguments`, `RESPONSES_BUILDERS` / `CHAT_BUILDERS`.
  - `mock/recorder.py`: `TrafficRecorder` (`next_seq`, `append`, `list`, `record`, `dropped`),
    `canonical_sha256`, `redact_headers`, `bearer_token`, `auth_status`.
  - `mock/app.py`: `create_mock_app(script, recorder, *, base_path, fake_key, max_requests)`,
    `responses_contract_violations`; routes `GET /models`, `POST /responses`,
    `POST /chat/completions`; unknown routes are recorded 404/405.
  - `core/models.py`: `Endpoint`, `RecordedExchange` (§17.3); `ScenarioResult.exchanges` is
    `list[RecordedExchange]`; `docs/report.schema.json` regenerated (drift test green).
  - `core/config.py`: `BHARATCODE_DOCUMENTED_MODELS`, `BHARATCODE_PRO_ONLY_MODEL`,
    `UNSUPPORTED_INPUT_TYPES`, `FILE_INPUT_KEYS` moved here from the scanner.
- Tests added (files, count) and gate results: `tests/mock/test_faults.py` (7),
  `test_payloads.py` (35), `test_recorder.py` (10), `test_app_nonstream.py` (80): 132 new,
  544 total. `ruff check .`, `ruff format --check .`, `pytest -m "not live" -q`,
  `python scripts/eval_fixtures.py --check` (107/107, 0 clean-fixture findings, dogfood 0)
  green on Python 3.12.14. Success bodies are validated with
  `openai.types.responses.Response`, `openai.types.chat.ChatCompletion` and
  `openai.types.Model`; one test drives the real `openai.OpenAI` client through the mock.
- Decisions made (ADR ids): ADR-014 (documented 400s), ADR-027 (shared facts in core, guard
  order, auth flag, redaction, non-stream meaning of stream actions, payload IDs, test client).
- Deviations from the plan and why:
  - **httpx2 ASGI (trap 2):** `httpx2/_transports/asgi.py` defines only
    `ASGITransport(AsyncBaseTransport)` with `handle_async_request`; there is no sync ASGI
    transport. Tests use `fastapi.testclient.TestClient` (Starlette 1.10.0, which imports
    `httpx2` and subclasses `httpx2.Client`). It also works as the OpenAI SDK's `http_client`.
  - The "flag" for a non-fake key is `response_summary["auth"] == "other_key"` plus the
    `[REDACTED]` header, not a new `RecordedExchange` field (keeps §17.3 as written).
  - Unknown routes and wrong methods are recorded (useful evidence of a wrong base URL), and the
    request limit counts every request, including rejected ones.
  - Response bodies leave the request's `tools` out (`tools: []`) so either tool style parses in
    the SDK model.
- Out-of-scope changes (file, reason, test):
  - `scanner/detect.py`, `scanner/rules/bharatcode.py`: constants moved to `core/config.py`
    (trap 1: the mock must not import the scanner). `detect` re-exports
    `BHARATCODE_DOCUMENTED_MODELS`; the rules alias `PRO_ONLY_MODEL`/`FILE_KEYS`. Behaviour
    unchanged; covered by the existing scanner tests and eval gates (all green).
- Known issues / limitations:
  - All error codes and the error body shape are [UNVERIFIED] (OpenAI conventions). The 401
    uses `invalid_api_key`; invalid JSON uses `invalid_json`.
  - Video input has no OpenAI item type, so nothing matches it (same as FC-BC-008). A `file_id`
    on an `input_image` item is rejected, like FC-BC-008 flags it; whether BharatCode accepts
    it is [UNVERIFIED].
  - `model` is not required by the mock (the default model is echoed); unknown models are
    accepted. Neither behaviour is documented.
  - `at_ms` counts from recorder creation, so one recorder per scenario is expected; the request
    limit is per recorder sequence too.
  - The 500 path does not catch failures inside `TrafficRecorder.record` itself.
- Exact next step for the next phase: Phase 5B — add the streaming branches in `mock/app.py`
  (replace the `ex.stream` 501 guard; documented 400s already run first), SSE builders in
  `payloads.py` and `mock/server.py` (`MockServer`, Uvicorn in a daemon thread on 127.0.0.1).
  Keep `response_summary` keys (`finish`, `call_ids`, `no_tools`, `tool_style`, `auth`,
  `violations`, `error_code`) stable for Phase 7 validators.
