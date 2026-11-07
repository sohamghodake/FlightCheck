# Phase 3 handoff — Rule engine, BharatCode contract rules, `scan`, JSON and terminal output

- Date / session: 2026-11-07, Claude Code chat for Phase 3.
- Status: done.
- What was built (files, public functions):
  - `scanner/engine.py`: `RuleMeta` (adds `kind`, default `static`), `RuleContext`
    (`provider_of`, `applies`, `call_sites_for`, `excerpt` (redacted, ≤ 300 chars), `finding`),
    `rule(meta, registry=None)`, `load_builtin_rules()`, `select_rules()` (unknown ID →
    `ConfigError` listing valid IDs), `run_rules(index, ctx, *, rule_ids, min_severity,
    registry)` with crash isolation (`rule_crashed` error diagnostic), low-provider-confidence
    downgrade, sort by `(path, line, rule_id)`, `sort_key()`.
  - `scanner/rules/{__init__,bharatcode}.py`: FC-BC-001…008 per §16.3, `doc_source`
    `https://bharatcode.ai/build`; FC-BC-007 is `hypothesis`; FC-BC-009 (P1) skipped/reserved.
  - `orchestrator.py`: `run_scan(ScanRequest, config) -> Report` (provider gating per ADR-025).
  - `reporting/json_report.py`: `report_to_json`, `write_report(report, out_dir)`,
    `read_report(path)` (unknown major → exit 2), `schema_json`, `export_schema(path)`;
    `python -m flightcheck.reporting.json_report docs/report.schema.json` regenerates the schema.
  - `reporting/terminal.py`: `render_scan` (header, findings grouped by file, diagnostics,
    summary). `cli/render.py`: `OutputFormat`, `ProviderChoice`, `FailOn`, `make_console`,
    `exceeds_fail_on`, `render_exit_line`, `render_error`, `render_internal_error`.
  - `cli/app.py`: `scan` command (§23.2 options plus `--no-color`, `-v`); exit codes 0/1/2/3.
  - `docs/report.schema.json`, `docs/RULES.md`, `tests/fixtures/rules/FC-BC-00{1..8}/` (27 files),
    `tests/fixtures/expected_findings.json` (13 expected findings).
- Tests added (files, count) and gate results: `tests/scanner/test_engine.py` (28),
  `tests/scanner/test_rules_fixtures.py` (41, manifest-parametrised), `tests/cli/test_scan_cli.py`
  (20), `tests/unit/test_schema_export.py` (3): 92 new, 305 total. `ruff check .`,
  `ruff format --check .`, `pytest -m "not live" -q` green on Python 3.12.14;
  `flightcheck scan tests/fixtures/rules --fail-on none` exits 0 (default `--fail-on high`
  exits 1; a missing path exits 2).
- Decisions made (ADR ids): ADR-012 (rule IDs permanent), ADR-025 (JSON always written, provider
  gating, one-hop containers, `--fail-on` and hint rules).
- Deviations from the plan and why:
  - The JSON report is always written (default `PATH/.flightcheck/`), so the terminal can show
    its path as in §23.3; `--format json` prints only that path (ADR-025).
  - Next-step hints don't mention `test`/`explain` yet, because those commands don't exist.
  - `ScanRequest.provider_override` is `str | None` as in §21.4; unknown values → exit 2.
  - There is no JSON Schema validator dependency, so "validates against exported schema" is
    covered by `Report.model_validate` plus the schema drift test.
  - FC-BC-002 with a variable keeps severity high, with confidence medium. FC-BC-007 on a raw post
    fires only when `json=` is given.
- Out-of-scope changes (file, reason, test):
  - `scanner/parser.py` (allowed additive): `CallSite.one_hop` and `_Scope.containers`,
    `_resolve_container`/`_one_hop`; tests in `tests/scanner/test_engine.py`
    (`test_one_hop_*`).
  - `scanner/parser.py` shadowing fix (found in post-commit review): one-hop lookups used an
    outer literal even when the name was rebound locally (parameter, `x = make()`, loop/`with`
    target, tuple unpacking, `+=`), giving false FC-BC-003/008 findings. New `_Scope.bound`,
    shared `_lookup()`, `visit_AugAssign`, `visit_For`/`visit_AsyncFor`, and parameters recorded
    as bound. The same fix applies to the Phase 2 string-constant (`base_url`) and env-name
    lookups. Tests: `test_one_hop_respects_local_shadowing` (8 cases),
    `test_one_hop_still_sees_outer_literals_when_not_shadowed`,
    `test_shadowed_tools_produce_no_finding`, `test_base_url_constant_respects_local_shadowing`.
  - `core/models.py` (allowed additive): `ScanRequest`; covered by engine/CLI tests.
  - `docs/ARCHITECTURE.md` "Current state" paragraph; `CLAUDE.md`, two rules added as instructed.
- Known issues / limitations: see `docs/RULES.md` (ADR-011 one-hop limits). One-hop containers see
  only bindings made before the call in source order. Excerpts are re-read from disk at rule time.
  Running `scan` without `--output` creates `PATH/.flightcheck/` (self-ignored).
- Exact next step for the next phase: Phase 4 with the plan's §28 Phase 4 prompt. Add
  `scanner/rules/{robustness,security}.py` with `providers=frozenset({"*"})`, reuse
  `RuleContext.finding`/`call_sites_for`, add fixtures under `tests/fixtures/rules/FC-GEN-*/` and
  entries to `tests/fixtures/expected_findings.json` (the parametrised test picks them up), and
  build `scripts/eval_fixtures.py --check` on the same manifest.
