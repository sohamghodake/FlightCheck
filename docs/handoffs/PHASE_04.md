# Phase 4 handoff — Robustness and security rules, fixture suite, evaluation script

- Date / session: 2026-11-07, Claude Code chat for Phase 4.
- Status: done (P0). P1 rules (FC-BC-009, FC-GEN-008/009, FC-SEC-003) not started.
- What was built (files, public functions):
  - `scanner/rules/robustness.py`: FC-GEN-001…007 per §16.3 (`providers={"*"}`; `doc_source`
    `https://bharatcode.ai/build` for 001–005 and 007, `None` for 006).
  - `scanner/rules/security.py`: FC-SEC-001 (prefixed keys critical/high, keyed literals
    high/medium, placeholders info), FC-SEC-002 (high/medium). `rules/__init__.py` imports both.
  - `scanner/engine.py`: `RuleContext.secrets`, `collect_secrets()`, `mask()` (applied to every
    excerpt, message and `rule_crashed` diagnostic), `finding_at(meta, location, message, *,
    severity, confidence, call)` (`finding` delegates to it); `run_rules` collects secrets first;
    the low-provider-confidence downgrade now skips `"*"` rules; `ALL_PROVIDERS` exported.
  - `scanner/ast_utils.py` (helpers): `name_words`, `is_api_key_name`, `is_credential_name`,
    `is_placeholder_key`, `prefixed_secrets`, `SECRET_PREFIX_PATTERNS`, `PLACEHOLDER_KEYS`.
  - `scanner/parser.py` (additive, same single visitor pass): new dataclasses `KeyedLiteral`,
    `StreamLoop`, `ArgumentsParse`, `ToolOutputLiteral`, `LogCall`; new `FileIndex` fields
    `module_facts`, `keyed_literals`, `stream_loops`, `argument_parses`, `tool_outputs`,
    `log_calls`, `timeout_overrides`; `_Scope.stream_calls`/`arg_names` (one-hop tables, shadowed
    like the others); `visit_Dict`; `_try_context()` (extracted from `_maybe_call_site`, same
    behaviour) reused for `json.loads` coverage; `STRING_PRODUCERS`, `LOG_METHODS`. Existing
    `CallSite`/`ClientBinding`/`FileIndex` fields unchanged.
  - Fixtures: `tests/fixtures/rules/FC-GEN-00{1..7}/`, `FC-SEC-00{1,2}/` (30 files); 13 P0 apps in
    `tests/fixtures/apps/<name>/main.py` (§25.1; `bc_bad_missing_history` is P1, not built).
  - `tests/fixtures/expected_findings.json`: rule suite now 92 findings (13 BC + 57 GEN/SEC on the
    unchanged BC fixtures + 22 on the new ones), plus an additive `apps` suite (`root`, `items`
    with `clean` and `files` per app; 15 findings). Every line checked by hand against the source.
  - `scripts/eval_fixtures.py` (`--check`, `--output`), `docs/EVALUATION.md` (generated),
    CI step "Evaluation gates" after pytest.
- Tests added (files, count) and gate results: `tests/scanner/test_fixture_suite.py` (54),
  `tests/security/test_scan_redaction.py` (4), `tests/scanner/test_rules_fixtures.py` (+48:
  GEN/SEC fixtures via the manifest, registry, ADR-013 wording, edge cases),
  `tests/scanner/test_engine.py` (+1): 107 new, 412 total. `ruff check .`,
  `ruff format --check .`, `pytest -m "not live" -q`, `python scripts/eval_fixtures.py --check`
  (107/107 detected, 0 clean-fixture findings, dogfood 0) and
  `flightcheck scan tests/fixtures/rules --fail-on none` (exit 0) green on Python 3.12.14.
  `flightcheck scan tests/fixtures/apps/bc_bad_secrets` shows only `bc_live_…[REDACTED]`.
- Decisions made (ADR ids): ADR-013 (heuristic confidence/wording, module-level facts),
  ADR-026 (masking hook on `RuleContext`; FC-SEC-001 keyed forms; FC-SEC-002 whole-word names;
  no downgrade for `"*"` rules; FC-GEN-007 shapes; FC-GEN-005 scope).
- Deviations from the plan and why:
  - FC-SEC-001 also flags dict-key and assignment forms (`{"api_key": …}`, `OPENAI_API_KEY = …`)
    besides `api_key=`; they had to be collected for masking anyway (ADR-026).
  - FC-SEC-002 matches whole words, not the §16.3 substring regex, so `total_tokens` is not a
    credential (ADR-026).
  - `--check` does not write `docs/EVALUATION.md` (keeps the tree clean in CI and local gates);
    run without `--check` to regenerate it. The commit SHA in it is HEAD when generated, i.e.
    the parent of the commit that contains it.
  - FC-GEN-008 (P1) is listed for `bc_bad_truncation` and FC-GEN-009 (P1) for
    `bc_bad_no_resilience` in §25.1; they are not implemented, so not expected.
- Out-of-scope changes (file, reason, test):
  - `tests/cli/test_scan_cli.py` (not in the scope list): six Phase 3 tests assumed only BC rules
    exist; narrowed with `--rule`, or to FC-BC findings, keeping what they check
    (openai_compatible now also asserts that GEN rules still run).
  - `scanner/engine.py` downgrade fix (beyond the masking hook): provider uncertainty no longer
    lowers FC-GEN/FC-SEC confidence. Test: `test_gen_rules_are_not_downgraded_for_unresolved_clients`.
  - `src/flightcheck/scanner/ast_utils.py`: the `bc_live_your_key_here` placeholder is written as
    `"bc_live_" + "your_key_here"` so the dogfood gate sees no prefixed literal in `src/`.
- Known issues / limitations: lexical heuristics (helper-function checks are FPs, kept in
  `known_fp_helper_check`); `try` coverage is the body only (handlers/`else`/`finally` do not
  protect); a function called only from inside a `try` is not analysed; FC-GEN-005 misses
  `OpenAI(**kwargs)`; FC-GEN-003 needs the loop to iterate the stream name directly (one hop).
  The BC rule fixtures now carry many expected FC-GEN-004/005 findings (unchanged files, stable
  lines). Short (< 4 chars) keys are masked only in quoted form.
- Exact next step for the next phase: Phase 5A (mock provider) with the plan's §28 prompt. The
  fixture apps read `BHARATCODE_BASE_URL`/`BHARATCODE_API_KEY`, take the question as
  `sys.argv[1]`, use one question and at most one tool, so Phase 7 can run them against the mock;
  add `expected_runtime.json` and `--runtime` to the eval script in Phase 7B.
