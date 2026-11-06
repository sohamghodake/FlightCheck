# Phase 2 handoff — Discovery, AST index, provider detection

- Date / session: 2026-11-06, Claude Code chat for Phase 2.
- Status: done.
- What was built (files, public functions):
  - `scanner/discovery.py`: `DiscoveryOptions` (+ `from_config(ScanConfig)`), `DiscoveryResult`
    (`root`, `files`, `skipped`, `diagnostics`, `total_bytes`, `relpath()`), `discover(target,
    options)`, `is_sensitive_filename()`. `os.walk(followlinks=False)` with in-place pruning,
    sensitive names never opened, NUL-byte check, size/count/total limits, `resolve_inside` on
    every file, symlink dedupe, single `.py` target.
  - `scanner/ast_utils.py`: `Unknown`, `attr_chain`, `split_attrs`, `literal_value`,
    `source_segment(max_len=300)`, `string_fragments` (one-hop name resolver optional).
  - `scanner/parser.py`: `ClientBinding`, `CallSite`, `EnvRead`, `FunctionFacts`,
    `StringLiteral`, `FileIndex` (`to_dict`, `counts`), `RepoIndex`; `decode_source`,
    `parse_source`, `index_file`, `build_index`. One NodeVisitor; receivers resolved after the
    file is visited (ADR-011).
  - `scanner/detect.py`: `Signal`, `DetectionResult`, `combine`, `binding_signals`,
    `call_site_signals`, `detect(index, declared_provider)`, `detect_providers()` (flat list),
    `BHARATCODE_DOCUMENTED_MODELS` (from BHARATCODE_CONTRACT.md; a test keeps them in step).
- Tests added (files, count) and gate results: `tests/scanner/test_{discovery,ast_utils,parser,
  detect}.py`, 114 new tests (213 total), fixtures in `tests/fixtures/parse/`. `ruff check .`,
  `ruff format --check .`, `pytest -m "not live" -q` green on Python 3.12.14 and 3.11; scanner
  tests clean with `-W error`; the `slow` 200-file test passes.
- Decisions made (ADR ids): ADR-011 (one-hop resolution), ADR-024 (detection combination rules).
- Deviations from the plan and why:
  - `detect()` returns a `DetectionResult` (per-binding, per-call-site, repo-level, diagnostics);
    `detect_providers()` keeps the ARCHITECTURE.md signature and returns the flat list.
  - Model-name signal is "supporting" and never breaks a tie against a direct signal (ADR-024).
  - Extras: `with ... as client` bindings, enclosing-function name lookup, PEP 263 coding
    cookies, `CallSite.params` (SDK kwargs or the raw post's `json=` dict), `client.beta.*`.
  - Syntax-error, non-UTF-8, BOM, tab and deep-nesting inputs are generated in tests (committed
    copies would break ruff).
- Out-of-scope changes (file, reason, test): `docs/ARCHITECTURE.md` "Current state" paragraph.
  No `core` changes.
- Known issues / limitations: see ADR-011 (closures over `self`, walrus, factories, arguments
  not resolved). `FileIndex.to_dict()` includes raw string literals: redact before printing.
- Exact next step for the next phase: Phase 3 with the plan's §28 Phase 3 prompt. Rules consume
  `RepoIndex.call_sites` (`params`, `stream`, `in_try`, `binding`) and `detect()` results;
  apply the low-provider-confidence downgrade there; copy ADR-011 limitations into `docs/RULES.md`.
