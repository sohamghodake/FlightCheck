# Phase 1 handoff — Core foundation: models, config, errors, logging, redaction, paths

- Date / session: 2026-11-06, run in the same Claude Code chat as Phase 0 (the developer's
  "chat 1").
- Status: done.
- What was built (files, public functions):
  - `core/errors.py`: `FlightCheckError(message, hint=None)` with class attribute `exit_code`
    (3); `ConfigError`, `ConsentRequiredError`, `PathOutsideRootError`, `NetworkDisabledError`,
    `UnsupportedCapabilityError` (exit 2); `ErrorKind` (§14.4); `ProviderError(kind, message, *,
    http_status, request_id, retryable, hint)` (exit 3; message redacted and cut to 500 chars).
  - `core/redact.py`: `redact(text, *, environ=None)`, `redact_obj(obj, *, environ=None)` (deep,
    new containers), `register_secret(value)`, `clear_registered_secrets()`. Patterns from §20.4
    plus exact env-value redaction (`[REDACTED:<ENV_NAME>]`).
  - `core/logging.py`: `get_logger(name=None)`, `configure_logging(verbosity)` (-1 error, 0
    warning, 1 info, 2+ debug; idempotent; stderr), `RedactingFilter`.
  - `core/paths.py`: `resolve_inside(root, candidate)`, `safe_relpath(path, root)`,
    `default_output_dir(target)` (creates `.flightcheck/.gitignore` with `*`).
  - `core/models.py`: §21.1–21.3 enums (`Severity` with `.rank`, `Confidence`, `FindingKind`,
    `Verification`, `CheckStatus`), `worst_status()`, and models `Location`, `Evidence`,
    `Finding` (+ `Finding.make_id`), `CheckResult` (passed needs evidence), `AppRun`,
    `ScenarioResult`, `ProviderDetection`, `Diagnostic`, `RepoMetadata`, `ReportMetadata`,
    `Summary` (+ `Summary.from_report_parts`), `Report`. `SCHEMA_VERSION = "1.0"`.
  - `core/config.py`: `TimeoutConfig`, `RetryPolicy` (+ `delay_for(attempt, jitter)`),
    `ProviderConfig`, `ScanConfig`, `ExpectConfig`, `TestConfig`, `ExplainConfig`,
    `FlightCheckConfig` (+ `api_key_for(name)`), `load_config(root, overrides=None, *,
    environ=None)`. BharatCode defaults copied from docs/BHARATCODE_CONTRACT.md §15.3.
- Tests added (files, count) and gate results: `tests/unit/test_redact.py`, `test_logging.py`,
  `test_paths.py`, `test_models.py`, `test_config.py`, `test_errors.py`, `test_core_imports.py`.
  96 new tests, 99 in total. `ruff check .`, `ruff format --check .` and
  `pytest -m "not live" -q` are green on Python 3.12.14 and 3.11.14; the suite is also clean with
  `-W error`. Manual check: `redact('key=bc_live_abcdef123456')` → `key=bc_live_…[REDACTED]`.
- Decisions made (ADR ids): ADR-010 (report schema 1.0 frozen, additive only), ADR-023 (core
  conventions: secret-key rule, env mapping, TOML layout, redaction order, placeholder types,
  model details). ADR-011…022 stay reserved for the later phases the plan assigns them to.
- Deviations from the plan and why:
  - The plan's secret-key rule (`(?i)(api_?key|token|secret)` with a value) would reject
    `api_key_env` and `max_output_tokens`. Only non-empty string values are rejected and `*_env`
    keys are exempt (ADR-023 item 1). `password` was added to the name pattern.
  - `Report.ai_suggestions` is a `list[dict]` placeholder, as the Phase 1 prompt says, so no
    `AISuggestion` model exists yet (§27 had only excluded `AIExplanation`).
  - `ProviderConfig.declared_capabilities` uses `str` keys: `Capability` belongs to Phase 6 and
    `core` must not import `providers`. This is a fourth placeholder beyond the three the prompt
    listed; it is recorded in ADR-023.
  - `ProviderConfig` has an extra `api_key: SecretStr | None` field (excluded from dumps) so the
    env-read key travels with its provider; `ExplainConfig` has `provider`, `max_calls` and
    `reasoning_none`, taken from §15.3 and §19.7.
  - Extra test files `test_errors.py` and `test_core_imports.py` (the second enforces the
    "core imports only stdlib + pydantic" acceptance criterion).
  - Request models (`ScanRequest`, `TestRequest`, `ExplainRequest`, §21.4) were not created: they
    belong with the orchestrator use cases (Phase 3 onwards).
- Out-of-scope changes (file, reason, test): `docs/ARCHITECTURE.md`, where the "Current state"
  paragraph now lists the `core` package. No code outside `core/` was changed.
- Known issues / limitations:
  - Environment-value redaction covers every variable whose name contains key, token, secret or
    password with a value of at least 8 characters. A non-secret variable matching that rule
    (e.g. a path in `SOME_KEY_DIR`) would also be redacted in output. This is intended as defence
    in depth.
  - The redaction registry (`register_secret`) is process-global; tests clear it with
    `clear_registered_secrets()`.
  - Phase 1 is committed locally only: pushing is still blocked by GitHub access, so CI has not
    run on GitHub yet.
- Exact next step for the next phase: Phase 2 (discovery, AST index, provider detection) with the
  plan's §28 Phase 2 prompt. Use `ScanConfig` limits/excludes from `core/config.py`,
  `resolve_inside` / `safe_relpath` from `core/paths.py`, and `Diagnostic` / `ProviderDetection` /
  `Location` from `core/models.py`. Scanner code must not import `providers`, `runtime`,
  `subprocess`, `importlib` or `runpy`.
