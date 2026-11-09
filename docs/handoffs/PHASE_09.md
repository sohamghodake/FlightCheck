# Phase 9 handoff — HTML report, `report` command, CI integration

- Date / session: 2026-11-09, one Claude Code chat (Phase 11 follows in the same chat, with a
  separate prompt).
- Status: done (P0-lite). The `ci` command and Markdown output (P1) were not built, as scoped.
- What was built (files, public functions):
  - `reporting/html_report.py`: `build_view(report) -> dict` (redacted, pre-grouped view
    model built from `redact_obj(report.model_dump(mode="json"))`), `render_html(report) ->
    str` (Jinja2 `Environment(autoescape=True, loader=PackageLoader("flightcheck.reporting"),
    undefined=StrictUndefined)`), `write_html(report, path)` (through `resolve_inside`),
    `HTML_FILENAME`, `LIMITATIONS`, `MOCK_FIDELITY_NOTICES`, `VERIFICATION_LABELS`,
    `STATUS_LABELS`.
  - `reporting/templates/report.html.j2`: one self-contained page, inline CSS (light and dark
    schemes), no JavaScript, no external assets. Sections: header and metadata; summary cards
    (severity, kind, hypotheses, the six check statuses, verification levels); providers and
    capability profile; findings grouped by file with evidence excerpts; runtime scenarios
    (overview table, a checks table per scenario, traffic and app output in `<details>`); AI
    suggestions (labelled with `AI_LABEL`, provenance, diffs in `<pre>`, fallback reason, hint
    and deterministic remediation); diagnostics; limitations and the mock-fidelity notice;
    generated-at footer.
  - `cli/app.py`: `flightcheck report REPORT.json` (`--format html|terminal|markdown`,
    `--output`, `--no-color`, `-v`); `--format html|all` on `scan`, `test` and `explain`;
    the `explain` hint in the `scan`/`test` exit lines; help text lists `report` as available
    and `ci` / Markdown as later. `cli/render.py`: `ReportFormat`, `render_exit_line(...,
    report_path=)`.
  - `scanner/parser.py`: route decorators are not raw HTTP calls (dogfood fix, see below).
  - `.github/workflows/ci.yml`: `eval_fixtures.py --check --runtime` and the dogfood step.
    `.github/workflows/flightcheck-example.yml` (manual only). `docs/CI.md`.
- Tests added (files, count) and gate results: `tests/unit/test_html_report.py` (22),
  `tests/cli/test_report_cli.py` (12), `tests/cli/test_scan_cli.py` (+4, replacing the 2
  "Phase 9" cases), `tests/cli/test_explain_cli.py` (+4, replacing 1), `tests/cli/test_test_cli.py`
  (+2 HTML cases, the 2 html/all usage-error cases removed), `tests/scanner/test_parser.py`
  (+1). 1322 total (+40). Gates green on Python 3.12.14 (macOS): `ruff check .`,
  `ruff format --check .`, `pytest -m "not live" -q` (1322 passed),
  `python scripts/eval_fixtures.py --check --runtime` (108/108, 0, 0; runtime 273/273, 0
  mismatches), dogfood `flightcheck scan src --fail-on low` exit 0 (no findings). Wheel:
  `uv build --wheel` contains `flightcheck/reporting/templates/report.html.j2`, and
  `render_html` works when imported from the wheel (zipimport). Mutation spot-checks, each
  caught by a failing test and reverted: `autoescape=False`; AI entries added to the
  findings total; a `<script>` in the template; the view built without `redact_obj`;
  hypotheses counted in the verification card; a wall-clock footer; `unsupported` shown as
  `skipped`; the report command's overwrite guard removed; the explain hint removed.
- Decisions made (ADR ids): ADR-021 (HTML has no JavaScript), ADR-032 (format outputs and
  file names, `report` defaults, view model, dogfood fix, CI and example trigger, exit-line
  hint).
- Deviations from the plan and why:
  - The limitations are a section near the end of the page ("Limitations and mock
    fidelity"), not literally inside `<footer>`; the footer keeps the generated-at line. Same
    content, easier to read.
  - The summary cards also show finding kinds and a findings column per verification level
    (§24 only names severity, status and verification). Hypotheses are kept out of the
    verification column and shown in their own card.
  - Request headers are not shown in the HTML traffic excerpts (bodies and response summaries
    are); the authorization value is already redacted, but headers add nothing to a review.
  - `report --format terminal --output FILE` is a usage error (exit 2) rather than ignored.
- Out-of-scope changes (file, reason, test):
  - `scanner/parser.py` (Phase 2/3): FastAPI route decorators `@app.post(f"{base}/responses")`
    in `mock/app.py` matched the raw `.post(url)` detection and produced 2× FC-GEN-004
    (medium), so the dogfood gate (`--fail-on low`) failed. Call nodes in a function's
    `decorator_list` are now skipped in the raw `.post` branch only. `expected_findings.json`
    unchanged, eval 108/108. Test:
    `tests/scanner/test_parser.py::test_route_decorator_post_is_not_an_api_call_phase9`.
  - Existing tests changed (QUALITY_GATES rule): the three "HTML report arrives in Phase 9"
    tests now assert the new behaviour; the two html/all entries in
    `test_test_cli.py::test_usage_errors_exit_2` moved to
    `test_html_formats_write_the_html_report`.
- Known issues / limitations:
  - CI changes are verified locally only: the GitHub run of `--runtime` and the dogfood step
    is pending until the push. The Phase 8 CI result could not be checked (no `gh` CLI here).
  - `actions/upload-artifact@v6` in the example workflow is **not verified** (no `gh`, no web
    access in this phase). The workflow is manual-only, so a wrong pin cannot break push CI;
    confirm the major before the first dispatch and record it in ADR-008.
  - Runtime findings have no file location, so they are grouped under "(repository)" like in
    the terminal.
  - No client-side sorting or filtering (no JavaScript by design). Very large reports are
    bounded by the clip limits (ADR-032 item 3), not paginated.
  - Only `http(s)` documentation URLs become links; any other `doc_url` is dropped.
  - Typer accepts `--format markdown` only on `report`; `scan`/`test`/`explain` have no
    Markdown option (P1).
- Manual verification (§27 item 19): `flightcheck scan tests/fixtures/apps/bc_bad_tool_pairing
  --output <tmp> --format all` (exit 1; tables, "HTML report written", exit line with the
  `flightcheck explain <tmp>/flightcheck-report.json` hint); `flightcheck explain
  <tmp>/flightcheck-report.json --llm fake --yes` (exit 0); `flightcheck report
  <tmp>/flightcheck-report.explained.json --output <tmp>/r.html` (exit 0). Also a
  `flightcheck test … --allow-exec --format html` report explained with `--format html`.
  Both opened in the built-in browser: header, metadata, summary cards (all six statuses,
  three verification levels, hypotheses card), providers, findings with snippets, the S01–S06
  overview and per-scenario check tables, traffic `<details>` with redacted bodies, the AI
  section (fake label "not generated by BharatCode"), limitations, mock-fidelity notice and
  footer all render. Two layout issues were fixed after viewing: narrow table columns broke
  words mid-word, and the app command lost its quoting (now `shlex.join`).
- Exact next step for the next phase: Phase 11 (security hardening, architecture tests, QA
  pass) with its own prompt. Read `docs/IMPLEMENTATION_STATUS.md`, this handoff and ADR-021 /
  ADR-032. Push Phases 8–9 first if not done and check that CI (including the new `--runtime`
  and dogfood steps) is green.
