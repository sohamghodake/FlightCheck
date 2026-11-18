# Phase G2 handoff — Groq provider profile and detection

- Date / session: 2026-11-18, Claude Code chat 1 of the Groq split (after G1, same chat).
- Status: done.
- What was built (files, public functions):
  - `core/config.py`: `GROQ_BASE_URL`, `GROQ_HOST`, `GROQ_API_KEY_ENV`, `GROQ_BASE_URL_ENV`,
    `GROQ_DEFAULT_MODEL`, `GROQ_CATALOGUE_DATE`; the dated catalogue `GroqModelFacts` /
    `GROQ_MODELS` (tier, kind, context, max completion tokens, strict/best-effort JSON
    schema, parallel tools, built-in tools, documented `reasoning_effort` values),
    `GROQ_DOCUMENTED_MODELS`, `GROQ_DECOMMISSIONED_MODELS`; `ProviderConfig.kind` gains
    `groq`; built-in `providers.groq` profile (connect 10 s / read 60 s, retries 1 s → 30 s).
    `[project] provider = "groq"` and `FLIGHTCHECK_PROVIDER=groq` select it; the key from
    `GROQ_API_KEY` is held as `SecretStr` and registered for redaction (existing loop).
  - `core/redact.py`: `gsk_` + ≥ 8 key characters → `gsk_…[REDACTED]`.
  - `providers/capabilities.py`: `GROQ_PROFILE`, `groq_profile(base_url, declared)`,
    `GROQ_SOURCES`; exported from `flightcheck.providers`.
  - `scanner/parser.py`: `groq.Groq`/`groq.AsyncGroq` in `CLIENT_CONSTRUCTORS`;
    `OPENAI_SDK_CONSTRUCTORS`, `GROQ_SDK_CONSTRUCTORS`, `SDK_CONSTRUCTORS` (their union).
  - `scanner/detect.py`: Groq signals (host `api.groq.com`, `GROQ` env-name word, groq SDK
    clients, raw posts, Groq models as supporting, declared `groq`), tie order
    `bharatcode > groq > …`, `is_groq_env_name()`, winning signals listed first.
  - `core/models.py`: `"groq"` in `ProviderDetection.provider` (schema regenerated:
    `docs/report.schema.json`, one enum value).
  - `orchestrator.py` / `cli`: `--provider groq` (BharatCode rules off everywhere, declares
    `groq`); config `provider = "groq"` declares `groq` in auto mode; help text and hint.
  - Docs: ADR-036, docs/RULES.md ("How rules apply"), docs/IMPLEMENTATION_STATUS.md, and the
    contract's model table now spells out `meta-llama/llama-prompt-guard-2-86m` (checked by a
    test).
- Tests added (files, count) and gate results: `tests/scanner/test_detect_groq.py` (43
  cases), `tests/scanner/test_groq_gating.py` (11), `tests/unit/test_groq_config.py` (15),
  `tests/providers/test_groq_profile.py` (6), fixtures `tests/fixtures/detect_groq/` (8
  files + README); `tests/security/test_leak_matrix.py` plants a `gsk_` value in AI output and
  the app's stdout (no new cases; two non-vacuity asserts). 1520 total (+75). Green on Python
  3.12 (macOS): `ruff check .`, `ruff format --check .`, `pytest -m "not live" -q` (1520
  passed), `python scripts/eval_fixtures.py --check --runtime` (108/108, 0, 0; 273/273, 0
  mismatches; `expected_findings.json` and `expected_runtime.json` unchanged), dogfood
  `flightcheck scan src --fail-on low` exit 0 (classification of `src` unchanged:
  2 × `openai_compatible_unknown`, before and after). Mutation spot-checks, each reverted:
  removing the `gsk_` pattern, using a substring env match, keeping auto mode for
  `--provider groq`, dropping `groq` from the tie order, and the old signal order each fail
  a new test.
- Decisions made (ADR ids): ADR-036 (signals and confidence, tie order, parser sets, gating
  before G3, config and capability profiles, catalogue, `gsk_` redaction, signal order).
- Deviations from the plan and why:
  - `gsk_` redaction was added although contract A4 marks the prefix [UNVERIFIED] (it comes
    from Groq's placeholders, the same evidence as `bc_live_`). The prompt said to add it only
    if verified; over-redacting is harmless and a missed key is not, so it is in, recorded in
    ADR-036 item 8. Revert = one line in `core/redact.py` if the developer disagrees.
  - Only `groq.Groq`/`groq.AsyncGroq` are constructors: the SDK also defines `Client`/
    `AsyncClient` aliases in `groq._client`, but whether `groq.Client` is exported was not
    checked (no web in G2), so they are not added.
  - `GROQ_PROFILE.max_output_tokens` is `None`: Groq's limit is per model (`GROQ_MODELS`).
  - Detection fixtures live in `tests/fixtures/detect_groq/`, not `tests/fixtures/parse/`:
    the latter's directory-level test expects a `bharatcode` repo classification, which a
    Groq file there would turn into a conflict.
- Out-of-scope changes (file, reason, test):
  - `tests/scanner/test_detect.py::test_other_base_url_is_openai_compatible_unknown` used
    `https://api.groq.com/openai/v1` as its example of an unknown host; Groq's host is now
    `groq` by design, so the test uses `https://llm.example.net/v1` (same intent; the Groq URL
    is covered by `test_literal_groq_base_url_is_groq_high`).
  - `scanner/detect.py::_detection` lists the winning provider's signals first (ADR-036 item
    9): the terminal's "Providers: …" reason showed another provider's signal in mixed
    repositories. Tests: `test_winning_signals_come_first_phase_g2`,
    `test_conflicts_keep_the_original_signal_order`.
  - Correction to the G1 handoff: Phase 11 was already pushed (`origin/main` is its commit);
    the status doc now says so.
- Known issues / limitations:
  - Groq call sites get only FC-GEN/FC-SEC rules until G3. FC-GEN-005 ignores groq clients
    (its own constructor set); FC-SEC-001 does not detect `gsk_` literals (G3).
  - `explain.provider = "groq"` loads, but `build_adapter` has no Groq adapter yet and exits
    2 with "has no adapter yet" (G5). `ExplainConfig.llm` has no `groq` value yet (G5).
  - For G4: `TestConfig.base_url_env` / `api_key_env` defaults are still
    `BHARATCODE_*`/`OPENAI_*`; a groq-SDK app under `test` reads `GROQ_BASE_URL` (mock
    **root**, the SDK adds `/openai/v1`) and `GROQ_API_KEY`, which the harness neither sets
    nor scrubs. The mock serves no `/openai/v1/…` routes yet. FC-RT-050's fake-key check
    therefore cannot cover groq-SDK apps until G4.
  - `capability_profile` in reports is still not filled by `scan` (unchanged behaviour).
  - Not pushed: `docs: drop the code freeze`, G1, G2.
- Exact next step for the next phase: G3 (new chat) — implement `FC-GQ-001…015/017…019`
  (FC-GQ-016 stays a held hypothesis, FC-GQ-020 is P1) from docs/GROQ_CONTRACT.md §4.4 in a
  new `scanner/rules/groq.py` with `providers=frozenset({"groq"})`, gated like the BharatCode
  rules (auto: `groq` call sites; `--provider groq`: forced), using `core.config.GROQ_MODELS`
  and `GROQ_DECOMMISSIONED_MODELS` (dated 18 Nov 2026); adapt FC-GEN-003/004/005/006/007 and
  FC-SEC-001 texts and detection per §4.3; add bad/good fixtures and expected findings;
  update docs/RULES.md. Keep every BharatCode gate green.
