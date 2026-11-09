# Phase 8 handoff — AI diagnostics and `explain`

- Date / session: 2026-11-09, one Claude Code chat.
- Status: done (P0). The §19.8 tool-using diagnostic agent (P1) and the malformed-JSON repair
  attempt (P1) were not built, as scoped. No live BharatCode run: there is no key
  ("BharatCode key available: no"), so §27 item 18's live check was not possible and was not
  simulated.
- What was built (files, public functions):
  - `core/models.py`: `AIPatch`, `AIExplanation` (§19.6, `extra="forbid"`, the max lengths),
    `AISuggestion` (§21.3 plus `rule_id`, `title`, `severity`, `finding_ids`, `remediation`),
    `AI_LABEL`; `Report.ai_suggestions: list[AISuggestion]` (ADR-023 item 7 placeholder
    tightened). `docs/report.schema.json` regenerated.
  - `diagnostics/schema.py`: re-exports, `AIExplanationBatch`, `explanation_json_schema()`
    (self-contained strict schema), `text_format()` (`flightcheck_explanations`, strict).
  - `diagnostics/contract_excerpts.py`: `VERIFIED`, `UNVERIFIED`, `RULE_TOPICS`,
    `excerpt_for`, `excerpts_for` (verbatim rows of `docs/BHARATCODE_CONTRACT.md` §15.1).
  - `diagnostics/sanitize.py`: `Sanitizer(repo_root)` with `text`, `obj`, `json_text`,
    `paths`; `fix_utf8`, `truncate`, `DROPPED_KEYS`.
  - `diagnostics/context.py`: `select(report, level, max_groups, repo, catalogue, sanitizer)
    -> Selection` (`Group`, `Instance`, notes, files); snippets via `resolve_inside`.
  - `diagnostics/prompt.py`: `INSTRUCTIONS`, `PROMPT_ONLY_SUFFIX`, `build_input`,
    `build_request` (→ `ResponsesRequest`), `preview_payload` (dry-run body, no key needed).
  - `diagnostics/fake_llm.py`: `FakeLLM.respond(request)`, `FAKE_PREFIX`, `FAKE_MODEL`.
  - `diagnostics/service.py`: `DiagnosticService.prepare(report, options) -> ExplainPlan`
    and `.explain(report, options, plan=None) -> Report`; `ExplainOptions`, `ConsentInfo`,
    `CallPlan`, `validate_batch`, `strip_code_fences`, `unified_diff`, fallback reason
    constants.
  - `orchestrator.py`: `check_catalogue`, `explain_service`, `plan_explain`, `run_explain`
    (re-exports `ExplainOptions`, `ExplainPlan`, `ConsentInfo` for the CLI).
  - `cli/app.py`: `flightcheck explain REPORT.json` with `--llm`, `--context`, `--repo`,
    `--max-findings`, `--model`, `--yes`, `--dry-run`, `--output`, `--format`, `--no-color`,
    `-v`; help lists `explain` as available. `cli/render.py`: `LLMChoice`, `ContextChoice`,
    `is_interactive`, `render_destination`, `render_consent_panel`, `render_dry_run`.
    `reporting/terminal.py`: `render_explain`, `ai_label`, `FALLBACK_HINTS`.
- Tests added (files, count) and gate results: `tests/diagnostics/test_service.py` (28),
  `test_validation.py` (18), `test_context.py` (20, incl. the contract drift test and an
  import-boundary test), `test_sanitize.py` (9, incl. the mandatory planted-secret test on
  the payload, the wire bytes and the explained report), helper `diag_support.py`;
  `tests/cli/test_explain_cli.py` (18); +1 in `tests/unit/test_redact.py`. 1282 total
  (+94). `ruff check .`, `ruff format --check .`, `pytest -m "not live" -q` (1282 passed),
  `python scripts/eval_fixtures.py --check` (108/108, 0, 0) and `--runtime --check` (273/273,
  0 mismatches) green on Python 3.12.14 (macOS); dogfood `flightcheck scan src/`: 0 FC-BC-*,
  0 FC-SEC-* (the two FC-GEN-004 in `mock/app.py` predate this phase). Mutation spot-checks,
  each caught by a failing test and reverted: an AI path dropping findings (invariant),
  consent bypass, no truncation retry, no verbatim-patch check, no redaction in the
  sanitiser, no finding-ID subset check.
- Decisions made (ADR ids): ADR-019 (AI output separate; never changes statuses), ADR-020
  (default context = metadata), ADR-031 (schema location, contract-excerpt dict,
  `needs_review` catalogue injected by the orchestrator, consent per backend, fallback reason
  names, retries, splitting, metadata invariant, sanitisation details, CLI, the redact fix).
- Deviations from the plan and why:
  - `AISuggestion` has five additive fields (ADR-031 item 1): a fallback entry must say which
    group it covers and carry the deterministic remediation it falls back to.
  - `summary.ai_suggestions` counts validated explanations only, not fallbacks.
  - `--dry-run` prints the BharatCode request bodies whatever `--llm` is (that is what it
    would send), and needs neither a key nor consent.
  - Snippets are exempt from §19.4's 2 000-character cap; §19.3's per-snippet and total
    limits apply instead (otherwise a 40-line snippet is routinely cut).
  - A local `unsupported_capability` refusal of `text.format` (a config that declares it
    unsupported) is treated like the documented 400: one prompt-only retry.
  - The `explain` next-step hint in `scan`/`test` exit lines (ADR-025 item 5) was not added,
    to keep the other commands' output unchanged in this phase.
- Out-of-scope changes (file, reason, test):
  - `core/redact.py` (Phase 1): the `key=value` pattern let the value swallow the backslash
    of a JSON-escaped quote, so re-redacting JSON text holding an already-redacted
    `api_key=[REDACTED]\"` deleted the backslash and broke the JSON. Found because the
    explain payload's `input` (a JSON document) became unparsable after the dry-run's
    defence-in-depth redaction. The value now ends at a backslash that escapes a quote; other
    backslashes are still part of the value. Test:
    `tests/unit/test_redact.py::test_key_value_pattern_keeps_json_text_valid_phase8`.
- Known issues / limitations:
  - No live run: the `text.format` sub-schema, error bodies and BharatCode's real answers to
    the prompt are unverified until Phase 10 (P-07). The prompt-only fallback is tested only
    against the mock's scripted 400.
  - A JSON-escaped secret written as `token=\"value\"` inside JSON text is not matched by the
    `key=value` pattern (pre-existing; `bc_live_`/`sk-`/Bearer/env/registered values still
    are). Explain redacts leaf strings before serialising, so its payloads are not affected.
  - An explanation for a rule that was not sent is dropped with a log warning only; it is
    not recorded in the report.
  - Any HTTP 400 on the first structured attempt triggers the prompt-only retry, since
    BharatCode's error body shape is [UNVERIFIED] and a format-specific 400 cannot be told
    apart.
  - `FLIGHTCHECK_NO_NETWORK` is checked when the adapter is built, after consent: with the
    switch on, `--llm bharatcode` still shows the panel (and exits 2 without `--yes` in CI).
  - The 24 000-character input cap is an assumption; the context window is undocumented.
  - The fake backend's text only restates rule metadata (by design, clearly labelled).
- Manual verification (§27 item 19): `flightcheck scan
  tests/fixtures/apps/bc_bad_tool_pairing --output <tmp>` (exit 1), then `flightcheck explain
  <tmp>/flightcheck-report.json --llm fake --yes --repo tests/fixtures/apps/bc_bad_tool_pairing`
  (exit 0): one group, FC-GEN-007 HIGH "Tool result output is not a string", labelled "AI
  suggestion (fake LLM, offline; not generated by BharatCode) — not verified", Summary / Root
  cause / Impact / Fix / Caveat / Confidence in a two-column grid with a hanging indent, then
  "1 explained, 0 fallback(s)" and the explained report path. `--dry-run` prints the
  destination, "Dry run: the 1 request(s) that --llm bharatcode would send", the full body
  (`store: false`, `max_output_tokens: 4000`, `reasoning`, `text.format`) and "nothing was
  sent". With a planted `bc_live_…` key and no TTY, `--context snippets` shows the consent
  panel (destination, model, snippets, 1 finding, 2 510 characters, `main.py`, the redaction
  sentence) and exits 2 with the `--dry-run`/`--yes` hint; the key never appears. Readable;
  the fake text is repetitive by design.
- Exact next step for the next phase: Phase 9 (HTML report, `report` command, CI workflows).
  Read `docs/IMPLEMENTATION_STATUS.md`, ADR-019/020/031 and this handoff. Render
  `report.ai_suggestions` as a separate section labelled with `AI_LABEL` (explanations,
  local diffs, fallback reason and deterministic remediation); never mix them into finding
  counts. Consider adding the `explain` hint to `scan`/`test` exit lines (ADR-025 item 5).
