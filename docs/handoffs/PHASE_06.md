# Phase 6 handoff — Provider adapters and capability registry

- Date / session: 2026-11-07, one Claude Code chat (Phase 6 only).
- Status: done (P0). The P1 items were **not** built: no `providers/openai_compat.py` and no
  `flightcheck providers` command; `build_adapter` raises `ConfigError` for
  `kind = "openai_compatible"`.
- What was built (files, public functions):
  - `core/capabilities.py`: `Capability`, `SupportLevel` (plus `declared_unsupported`),
    `CapabilityEntry`, `CapabilityProfile` (`entry()`, `level()`), `UNSUPPORTED_LEVELS`.
  - `core/contract.py`: `Violation`, `responses_contract_violations` (moved unchanged from
    `mock/app.py`; the mock imports it now).
  - `core/models.py`: `Report.capability_profile: CapabilityProfile | None`;
    `core/config.py`: `ProviderConfig.declared_capabilities: dict[Capability, bool]`;
    `docs/report.schema.json` regenerated (additive).
  - `providers/capabilities.py`: re-exports; `BHARATCODE_PROFILE`, `bharatcode_profile(base_url,
    declared)`, `generic_profile(declared, base_url, provider)`, `BHARATCODE_SOURCE`.
  - `providers/base.py`: `FinishState`, `chat_finish_state`, `responses_finish_state`,
    `ToolCall`, `Usage`, `NormalizedResponse` (`executable_tool_calls()`, `is_complete`,
    `unverified_capabilities`), `StreamEvent` (`terminal`), `ResponsesRequest`, `ChatRequest`,
    `ModelInfo`, `RawResponse`, `ProviderAdapter` ABC (context manager); `Endpoint` re-used
    from core.
  - `providers/sse.py`: `iter_sse_lines`, `parse_sse` → `SSEMessage`, `responses_events`,
    `chat_events`, `SSEProtocolError`, reason constants (`EOF_REASON`, ...).
  - `providers/http.py`: `build_client`, `network_disabled`, `kind_for_status`,
    `error_from_response`, `error_from_transport`, `parse_json_body`, `parse_retry_after`,
    `retry_after_of`, `request_id_of`, `send_with_retry`, `HTTPX_ERRORS`.
  - `providers/bharatcode.py`: `BharatCodeAdapter(config, transport=None, sleep=time.sleep, *,
    jitter=None)` with `list_models`, `create_response`, `stream_response`,
    `create_chat_completion`, `stream_chat_completion`, `preflight`, `responses_payload`,
    `chat_payload`, `unverified_capabilities`, `raw_post`, `close`; `capabilities_used`.
  - `providers/registry.py`: `build_adapter(provider_config, *, transport=None, sleep=...)`.
- Tests added (files, count) and gate results: `tests/providers/test_capabilities.py` (16, incl.
  the import-boundary tests), `test_sse.py` (75, incl. every mock stream builder × action),
  `test_http_retry.py` (52), `test_provider_errors.py` (108), `test_bharatcode_adapter.py` (106);
  helper `tests/providers/provider_bridge.py`. 357 new, 982 total. `ruff check .`,
  `ruff format --check .`, `pytest -m "not live" -q` (982 passed) and
  `python scripts/eval_fixtures.py --check` (107/107, 0, 0) green on Python 3.12.14.
  Mutation spot-checks (each broke ≥ 1 test, then restored): `store` not forced, no EOF
  synthetic `failed`, retry everything, no `model_not_in_plan` hint, truncated tool calls
  marked complete, no no-network check, no raise after `failed`, `Retry-After` ignored,
  keepalive turned into text.
- Decisions made (ADR ids): ADR-016 (sync adapters, injected transport/sleep, retry and stream
  rules; confirms ADR-003), ADR-028 (capability types in core, `declared_unsupported`, shared
  contract checks, pre-flight details, unverified tagging, `raw_post`).
- Deviations from the plan and why:
  - Test transport: `httpx2.MockTransport` + `fastapi.testclient.TestClient(create_mock_app())`
    (sync), not `ASGITransport` (async-only) and not `MockServer` (sockets; this prompt
    superseded the 5B suggestion). Verified: request headers/body reach the mock, the mock's
    headers (`x-request-id`, `content-type`, `retry-after`) and SSE bytes come back intact;
    bodies are re-chunked into 7-byte pieces to exercise line buffering.
  - `tests/providers/test_errors.py` is named `test_provider_errors.py`: the test tree has no
    `__init__.py`, so a second `test_errors.py` (besides `tests/unit/test_errors.py`) fails
    collection with a basename clash.
  - SSE lines are split from `iter_text()` on CR/LF only (`iter_sse_lines`) rather than with
    `iter_lines()`, which also splits on U+2028/U+0085 that can appear unescaped in JSON
    (test: `test_iter_sse_lines_keeps_unicode_line_separators_inside_lines`). `parse_sse`
    still accepts `iter_lines()` output.
  - Pre-flight refusals raise `ProviderError(UNSUPPORTED_CAPABILITY | BAD_REQUEST)`, not
    `UnsupportedCapabilityError` (plan §14.1), so every operation error is one type (ADR-016).
  - `Endpoint` is the existing core enum (`chat.completions`), not a new provider enum with
    `chat_completions`.
  - Declarations use the TOML table `[providers.<name>.declared_capabilities]` (the field's
    name), not `[providers.<name>.capabilities]` as the plan wrote.
  - Added (not in §14.3): `NormalizedResponse.unverified_capabilities` and `is_complete`,
    `StreamEvent.terminal`, `RawResponse`, `SupportLevel.DECLARED_UNSUPPORTED`.
- Out-of-scope changes (file, reason, test): `mock/app.py` — the documented-400 check moved to
  `core/contract.py` so the adapter and mock share one implementation (allowed by the prompt);
  behaviour unchanged, the full mock suite (189 fast + 24 slow) passes unchanged, and the
  moved code was diffed line by line against HEAD. No earlier-phase bugs were found.
- Known issues / limitations:
  - Manual verification (plan §27 item 19) skipped: no BharatCode key is available yet.
  - Everything beyond the documented facts is [UNVERIFIED] until Phase 10: error body shape
    and codes, 429 and `Retry-After`, Chat stream format and `[DONE]`, Responses stream
    payload fields, keepalive text, usage field names, the `/models` body shape, non-stream
    `completed`/`failed` status names, and Chat `stop` + tool calls.
  - `ProviderError` has no `retry_after` field; `error_from_response` attaches
    `retry_after_s` as an attribute (read with `retry_after_of`), because `core/errors.py` was
    out of scope. A later phase may add the field properly.
  - Connect timeouts are classed `TIMEOUT` and not retried, although no request was sent.
  - A Chat stream ends at the `finish_reason` chunk, so a usage chunk sent after it is not
    read. Streams are not tagged with `unverified_capabilities` per event; callers use
    `adapter.unverified_capabilities(payload, endpoint)`.
  - A server that sends SSE without the final blank line loses its last event (SSE rules), so
    such a stream ends as `failed`.
- Exact next step for the next phase: Phase 7A (runtime harness, validators, scenarios
  S01/S02/S04), from the plan's "Prompt — Phase 7A". The runtime does not need the adapters;
  Phase 8 (`explain`) uses `build_adapter(config.providers[...])` with
  `ResponsesRequest(text_format=..., reasoning_effort="none", max_output_tokens=4000)` and
  must refuse to parse anything whose `is_complete` is false.
