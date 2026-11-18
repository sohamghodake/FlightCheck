# Phase G3 handoff — Groq contract rules

- Date / session: 2026-11-18, Claude Code chat 2 of the Groq split (G3 only).
- Status: done. All P0 rules and all P1 rules named in the prompt are implemented; FC-GQ-016
  is held until G5 and FC-GQ-020 is reserved (P1), as planned.
- What was built (files, public functions):
  - `scanner/rules/groq.py` (new): FC-GQ-001…015 and 017…019 (`providers={"groq"}`, category
    `groq-contract`), each tracing to docs/GROQ_CONTRACT.md rows (comment headers and messages
    cite them); per-rule Groq doc URLs (`DOC_*`); `FREE_TIER_SHUTDOWN_MODELS` derived from the
    catalogue. Registered between the BharatCode and robustness rules, in numeric order.
  - `scanner/engine.py`: `ProviderMode` (`BharatCodeMode` and new `GroqMode` are aliases),
    `RuleContext.groq_mode`, `RuleContext.mode_for(meta)`, `RuleContext.applies_to_binding()`,
    optional `remediation`/`doc_url` overrides on `finding()`/`finding_at()`; `run_rules` and
    the downgrade use the family's mode. `RuleMeta.category` gains `"groq-contract"`.
  - `orchestrator._provider_mode` returns `(bharatcode_mode, groq_mode, declared)` (ADR-037
    table); `run_scan` and `run_test` pass `groq_mode`. `--provider` help text updated.
  - `core/config.py`: `GroqModelFacts.reasoning_format` (False for the two GPT-OSS models).
  - `scanner/ast_utils.py`: `gsk_` prefix pattern (≥ 8 key characters) and the Groq
    placeholders `gsk_your_secret_key_here`, `gsk_...` (split in source for the dogfood gate).
  - `scanner/rules/robustness.py`: Groq wording/remediation/doc links on `groq` call sites for
    FC-GEN-001…004, file-level for FC-GEN-006/007; FC-GEN-005 checks `groq.Groq`/`AsyncGroq`
    (info) and states each library's own default timeout.
  - `scanner/rules/security.py`: FC-SEC-001 knows `gsk_`; remediation names `GROQ_API_KEY`,
    `BHARATCODE_API_KEY` or neutral wording by the key's provider.
  - Docs: docs/RULES.md (Groq section, mode table, GEN/SEC rows, limitations), ADR-037,
    docs/ARCHITECTURE.md (module list), docs/EVALUATION.md (regenerated), this handoff, status.
- Tests added (files, count) and gate results:
  - `tests/scanner/test_groq_rules.py` (37), `tests/scanner/test_groq_rule_gating.py` (25:
    every mode-table row, rule-level cells for every setting, downgrade on/off, FC-GQ-015
    binding gating), `tests/scanner/test_groq_adaptations.py` (21: GEN/SEC texts on Groq and
    unchanged BharatCode texts, `gsk_` masking across JSON/HTML/terminal, dogfood of `src`,
    `explain --llm fake` on a report with FC-GQ findings); registry tests in
    `test_engine.py`/`test_rules_fixtures.py` extended (Groq bad fixtures must carry the
    "INTENTIONALLY FLAWED FIXTURE" marker too); G2's `test_groq_gating.py` mode-table
    expectations moved to the three-value form.
  - Fixtures: 67 files — `tests/fixtures/rules/FC-GQ-0NN/` (bad and good for each of the 18
    rules), `FC-SEC-001/bad_groq_key.py`, `FC-SEC-001/good_groq_placeholders.py`,
    `FC-GEN-005/good_groq_sdk_default.py`; 67 manifest entries added, every finding
    hand-checked against the source (line, severity, confidence per §4.4).
  - 1691 tests (+171). Green on Python 3.12 (macOS): `ruff check .`, `ruff format --check .`,
    `pytest -m "not live" -q` (1691 passed), `python scripts/eval_fixtures.py --check
    --runtime` (156/156, 0, 0; 273/273, 0 mismatches), dogfood `flightcheck scan src --fail-on
    low` exit 0 (no findings). `expected_findings.json`: purely additive (108 → 156 expected
    keys; every pre-existing entry and the apps section unchanged, checked by comparing the
    parsed manifest with `HEAD`);
    `expected_runtime.json` unchanged.
  - Mutation spot-checks, each reverted, each killed by a new test: removing the `gsk_`
    pattern; downgrading on `bharatcode_mode` only; dropping the Groq branch of `mode_for`;
    flagging `logprobs=False`; leaving Groq rules on under `--provider bharatcode`; FC-GEN-005
    groq at low instead of info; FC-GQ-014 "raw" firing without JSON mode or tools.
- Decisions made (ADR ids): ADR-037 (mode table and engine API; category; status claims only
  for C2/C4/M4/S8/T3; FC-GQ-006 Enterprise-only Llama models at low; FC-GQ-011 catalogue
  models and `json_schema` only; FC-GQ-014 S8 = `json_object`; variable values for 002/004/010
  at lower confidence; FC-GQ-019 skips a variable `stream`; FC-GQ-015 httpx needs a bound
  post; FC-GEN-005 groq = info; FC-GEN-003 does not accept `x_groq.error` alone; FC-SEC-001
  remediation by key provider).
- Deviations from the plan and why:
  - FC-GQ-019: §4.4 says "`stream` not literal True"; a variable `stream` is not flagged
    (`stream=stream` with `stream_options` is a common correct pattern). ADR-037 item 3.
  - FC-GQ-011 detection: §4.4's "`response_format`/`text.format` with stream or tools" is
    narrowed to `json_schema` (Structured Outputs proper); `text.format` `{"type": "text"}` is
    the default and JSON object mode is a separate feature. An unknown model is left to
    FC-GQ-006.
  - FC-GQ-015 for `httpx` clients flags only with a resolved post path lacking `/openai/v1`
    (host-only `base_url` plus `/openai/v1/...` paths is valid).
  - FC-SEC-001's default remediation is now provider-neutral (it named `BHARATCODE_API_KEY`
    for every key, including `sk-` and `OPENAI_API_KEY` literals); BharatCode keys keep the
    old text. Remediation text is not part of any manifest.
  - FC-GEN-005's message no longer claims the OpenAI 10-minute default for `httpx` clients and
    drops "BharatCode requests may queue" for non-BharatCode clients (the BharatCode text is
    pinned by a test).
- Out-of-scope changes (file, reason, test):
  - None outside G3's scope. Note: `tests/fixtures/rules` now holds high-confidence Groq and
    BharatCode signals, so its repo-level detection is `unknown` with a `provider_conflict`
    info diagnostic; no existing finding changes (the two unresolved call sites there carry
    their own BharatCode signals). Covered by the unchanged manifest.
- Known issues / limitations:
  - Groq rules inspect literal requests only: `chat.completions.parse(response_format=Model)`,
    `extra_body` and SDK-built requests are not seen; a model in a variable gets no
    model-dependent check (docs/RULES.md limitations).
  - Every [UNVERIFIED] effect (R4 `store`/`include`…, C5, S5/S7, R9 on other models) is worded
    as such; G5 verifies and may raise or lower severities.
  - FC-GQ-016 (message `name`) waits for G5 (C2/C3 conflict). FC-GQ-020 is P1.
  - `docs/ARCHITECTURE.md`'s "Current state (Phase 11)" header paragraph is older than G2/G3
    beyond the module list updated here.
  - The HTML report's limitation text says non-literal values never produce a contract
    violation; FC-BC-002 and FC-GQ-002/004/010 flag a supplied parameter whose value is a
    variable (lower confidence). Unchanged wording; worth aligning in G6's docs cleanup.
  - Not pushed: G3 (everything through G2 is pushed; CI green on 3.11/3.12/3.13).
- Exact next step for the next phase: G4 (new chat) — Groq mock, scenarios and runtime checks
  from docs/GROQ_CONTRACT.md §4.5–4.7 (items marked G4): `/openai/v1/…` mock routes and Groq
  payload shapes (`x_groq`, `x_groq.error` stream chunks, error bodies with
  `failed_generation`), harness support for `GROQ_API_KEY`/`GROQ_BASE_URL` (groq SDK base URL
  is the mock root; the SDK adds `/openai/v1`), Groq fixture apps with `[test]` sections and
  runtime expectations, keeping `expected_runtime.json`'s BharatCode rows unchanged.
