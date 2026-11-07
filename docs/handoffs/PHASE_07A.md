# Phase 7A handoff — Runtime harness, validators, scenarios S01/S02/S04

- Date / session: 2026-11-07, one Claude Code chat (interrupted once by a usage limit; resumed
  in the same chat).
- Status: done (P0). The `test` CLI command, S03/S05/S06 and `[test.expect]` are Phase 7B.
- What was built (files, public functions):
  - `runtime/harness.py`: `ensure_consent(allow_exec, target)`, `make_fake_key()`,
    `build_child_env(parent_env, test_config, mock_url, fake_key, *, guard_port=None)`,
    `run_app(command, cwd, env, stdin, timeout_s, *, abort_when=None, path_aliases=None) ->
    AppRun`, `resolve_cwd`, `unused_local_port`, `AppLaunchError` (hint "check [test].command").
    Popen with a list, `shell=False`, new session (POSIX) / `CREATE_NEW_PROCESS_GROUP`
    (Windows); terminate → 3 s → kill the group on timeout or abort, and kill leftovers in the
    group after a normal exit. Reader threads keep ≤ 1 MiB per stream; excerpts are decoded
    with `errors="replace"`, root/home paths aliased, redacted, cut to 8 KB.
  - `runtime/scenarios.py`: `Scenario(id, name, script_factory, check_ids,
    default_expectations, priority)` + `all_check_ids`; `S01`, `S02`, `S04`, `SCENARIOS`,
    `KNOWN_SCENARIO_IDS`, `PHASE_7B_SCENARIOS`, `P1_SCENARIOS`, `EVERY_SCENARIO_CHECK_IDS`.
  - `runtime/validators.py`: one function per check (`check_rt001` … `check_rt060`,
    `VALIDATORS`, `CHECKS` metadata), `CheckConfig`, helpers (`model_exchanges`,
    `split_endpoints`, `tool_results`, `issued_call_ids`, `gaps_ms`, `sdk_retry_count`,
    `user_texts`), `evaluate`, `report_once`, `skip_all`, `error_all`, `to_finding`.
  - `orchestrator.run_test(TestRequest, config, *, environ=None) -> Report` and
    `hardcoded_base_urls(index)`; `core/models.TestRequest` (§21.4; not in the report schema,
    schema drift test green).
  - `tests/fixtures/apps/*/flightcheck.toml` (all 13 apps: `[test]` only, `timeout_s = 20`),
    `tests/fixtures/expected_runtime.json` (S01/S02/S04 per app).
- Tests added (files, count) and gate results: `tests/runtime/test_harness.py` (24),
  `test_validators.py` (68), `test_scenarios_7a.py` (44); 27 of them `slow`. 1118 total
  (+136). `ruff check .`, `ruff format --check .`, `pytest -m "not live" -q` (1118 passed, 52 s)
  and `python scripts/eval_fixtures.py --check` (108/108, 0, 0) green on Python 3.12.14 (macOS).
  Mutation spot-checks (each caught by a failing test, then reverted): env copied from the
  parent, no process-group kill, consent bypassed, no redaction, pre-flight off, RT-012 always
  passing, RT-013 off, RT-014 input check off, RT-041 SDK attribution off, RT-050 off,
  `report_once` off.
- Decisions made (ADR ids): ADR-017 (consented subprocess only), ADR-018 (needs_review without
  expectations), ADR-029 (pre-flight placement, report-once rules, SDK attribution, FC-RT-008,
  unimplemented scenarios, request-limit abort, details).
- Deviations from the plan and why:
  - **New check FC-RT-008 "Baseline run exits 0"** (S01, severity info, never `failed`). §18.3
    requires S01 with a non-zero exit to be `needs_review` but names no check, and a scenario's
    status comes from its checks (ADR-029 item 4).
  - FC-RT-050 and FC-RT-060 are reported once like FC-RT-001…007 (the prompt names only
    001…007). Reporting them per scenario made S02/S04 show `passed` for an app whose S02/S04
    checks were all skipped after a contract 400 (bc_bad_stateful). A new offending request in a
    later scenario is still shown, and every request-limit breach is kept (ADR-029 item 2).
  - FC-RT-041 is `needs_review` whenever every retry carries the SDK's retry count, not only
    when the gap is < 500 ms: measured SDK first gaps were 440–510 ms, so the threshold would
    flap between `passed` and `needs_review` for the same app (ADR-029 item 3).
  - The harness stops the app once it exceeds `max_requests` (plan: "scenario aborted").
  - FC-RT-001 treats `store: null` like an omitted `store` (`needs_review`); any other value
    except `false` fails. FC-RT-007 fails a missing `model` (documented as required).
  - Integration covers all 13 fixture apps (plan item 14 names five), frozen in
    `expected_runtime.json`.
- Out-of-scope changes (file, reason, test):
  - Fixture fix `tests/fixtures/apps/bc_bad_stateful/main.py`: the first request now also sends
    `previous_response_id` (a saved conversation id). Before, the mock's 400 for `store=true`
    stopped the app before any `previous_response_id` was sent, so RT-002 could not fail as plan
    item 14 requires. Static expectations updated in `expected_findings.json` (FC-BC-001/002 at
    L24 and L27; one more FC-BC-002), eval 107 → 108 expected findings, `docs/EVALUATION.md`
    regenerated. Tests: `test_fixture_suite.py`, eval `--check`,
    `test_item_14_acceptance_details`.
  - No changes to `providers/`, the mock's behaviour or the scanner.
- Findings while implementing:
  - Egress guard verified with a child script (`test_egress_guard_lets_the_sdk_reach_the_mock_
    and_blocks_other_hosts`): with `NO_PROXY=127.0.0.1,localhost` the SDK reaches the mock; a
    request to a non-local host goes to the dead proxy and fails at once with `ConnectError`
    (connection refused), so nothing leaves the machine. httpx2 2.16 `get_environment_proxies`
    (`_utils.py:31-80`) and `trust_env=True` (`_client.py:658`).
  - OpenAI SDK 3.46.0 (uses httpx2): `DEFAULT_MAX_RETRIES = 2`, `INITIAL_RETRY_DELAY = 0.5`,
    jitter `1 − 0.25·random()` (`_constants.py`, `_base_client.py:898-920`), header
    `x-stainless-retry-count` (`_base_client.py:551`), kept by the recorder (its name matches no
    secret pattern). So S02 passes FC-RT-040 for an app that does nothing; the message says so.
  - On POSIX, `Popen(env=...)` looks the command up on the child env's PATH, so tests put
    `Path(sys.executable).parent` first; the toml files say just `python`.
  - The `api_key=...` redaction pattern masks `OPENAI_API_KEY=<fake key>` in child output too.
- Manual verification (§27 item 19): `PATH=.venv/bin:$PATH python -c "...run_test(TestRequest(
  root='tests/fixtures/apps/bc_bad_tool_pairing', allow_exec=True, scenarios=['S04']), ...)"`.
  The S04 ScenarioResult JSON showed status `failed`; exchange 1 `tool_call` with
  `call_ids: ["call_fc_S04_1"]`, exchange 2 carrying `function_call_output` with
  `call_id: "call_1"` and a dict output; both requests `authorization: "[FAKE KEY]"`,
  `auth: fake_key`. Checks: RT-011 passed, RT-012 failed (observed `"call_1"`), RT-013 failed
  (`dict`), RT-014 passed, contract checks passed. The app itself exited 0 (the mock does not
  reject a mismatched call id; what BharatCode does is [UNVERIFIED]).
- Known issues / limitations:
  - The egress guard is best effort, not a sandbox (raw sockets, non-proxy clients, file
    access are not stopped); no memory/CPU limits (§20.7, P2).
  - Windows process-group handling (`CTRL_BREAK_EVENT`, `taskkill /T`) is written but untested.
  - After a normal exit the harness SIGKILLs the child's (former) process group; a grandchild
    that called `setsid` escapes it, and its pipe reader is abandoned after 2 s.
  - `at_ms` is recorded when an exchange is answered, so FC-RT-041 gaps include request
    handling time (a few ms).
  - The pre-flight sees only literal/one-hop base URLs on clients and raw-call URLs; an
    f-string host or a URL built at runtime is not detected (the app then sends no traffic to
    the mock, and checks are `skipped` with the §17.2 reason, or the guard blocks it).
  - `openai_plain` fails FC-RT-007 (`gpt-4o-mini` is not a BharatCode model): true for
    BharatCode, recorded in `expected_runtime.json`.
  - All mock payload shapes beyond the documented facts stay [UNVERIFIED] until Phase 10.
- Exact next step for the next phase: Phase 7B (scenarios S03/S05/S06, `[test.expect]`,
  FC-RT-020/022/023/030, the `test` CLI command with the warning panel and `--allow-exec`,
  `eval_fixtures.py --runtime`). Add the scenarios to `runtime/scenarios.SCENARIOS` (remove them
  from `PHASE_7B_SCENARIOS`), validators to `VALIDATORS`/`CHECKS`, and S03/S05/S06 entries to
  `expected_runtime.json`; the CLI calls `orchestrator.run_test` and maps
  `ConsentRequiredError` → exit 2, `MockServerError` → exit 3.
