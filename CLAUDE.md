# CLAUDE.md — FlightCheck

FlightCheck is a local-first Python CLI (a portfolio project) that finds Groq API integration
mistakes in Python AI apps via static analysis, a traffic-recording contract mock, failure
scenarios, and opt-in Groq-powered explanations. Groq replaces BharatCode (ADR-035): the
BharatCode path keeps working until phase G6 removes it.

## Where the plan lives

Phase prompts for the Groq phases G1–G6 come from the developer in chat; the contract and
migration map are in docs/GROQ_CONTRACT.md (§4 says which phase does what).

The original implementation plan is at `docs/FLIGHTCHECK_IMPLEMENTATION_PHASE_PLAN.md`. It is
**local-only**: it is git-ignored on purpose and is not in the public repository (see ADR-000 in
docs/DECISIONS.md). Never commit it, never remove its `.gitignore` entry, and never make CI or
tests depend on it. It is BharatCode-era reference, not authority: once an ADR exists, the ADR
wins. If a prompt needs it and the file is missing, stop and ask the developer to restore it from
their backup.

## Before you change anything

1. Read: docs/IMPLEMENTATION_STATUS.md, docs/DECISIONS.md, docs/ARCHITECTURE.md, the latest
   docs/handoffs/PHASE_*.md, docs/GROQ_CONTRACT.md, and any plan sections your prompt names.
2. Run `git status` and `git log --oneline -15`. If the tree is dirty or the previous phase is not
   marked done, stop and report.
3. Work only on the phase you were given. Never start the next phase.

## Non-negotiable rules

- Never invent Groq endpoints, models, parameters, events or error formats. Use
  docs/GROQ_CONTRACT.md. Keep [UNVERIFIED] labels until G5 verifies them live.
  (docs/BHARATCODE_CONTRACT.md stays the reference for the BharatCode path until G6.)
- Never execute repository code outside runtime/harness.py, and only behind --allow-exec.
- Never send anything to an external API without the consent flow in diagnostics/service.py.
- Never print, log, or write secrets; all output goes through core/redact.py.
- Never collapse statuses: passed/failed/skipped/unsupported/needs_review/error; verification
  levels static/runtime_mock/runtime_live; AI output is separate and never changes statuses.
- Respect import boundaries (docs/ARCHITECTURE.md): scanner must not import providers, runtime,
  subprocess, importlib or runpy.
- No real network in tests (FLIGHTCHECK_NO_NETWORK=1). Live tests are marked `live`.
- Do not rewrite working code to suit a new idea. Minimal out-of-scope fixes need a test and a
  note in the handoff.
- No new dependencies without an ADR.
- Commits are authored by the developer only: never add Co-Authored-By or any AI attribution.
- Read the plan only by section line ranges (grep headings first); never read it whole.

## Commands

- Install: `pip install -e ".[dev]"` (or `uv sync --extra dev`)
- Gates: `ruff check . && ruff format --check . && pytest -m "not live" -q`
- Eval: `python scripts/eval_fixtures.py --check` (from Phase 4; add `--runtime` after Phase 7B)

## Finishing a phase

1. All gates green.
2. Update docs/IMPLEMENTATION_STATUS.md (status, date, summary, next phase).
3. Append ADRs to docs/DECISIONS.md for any decision.
4. Write docs/handoffs/PHASE_<ID>.md using the template below.
5. Commit with the message from your prompt.
6. Print the completion report and STOP.

## Handoff template

```markdown
# Phase <ID> handoff — <title>

- Date / session:
- Status: done | partial (explain)
- What was built (files, public functions):
- Tests added (files, count) and gate results:
- Decisions made (ADR ids):
- Deviations from the plan and why:
- Out-of-scope changes (file, reason, test):
- Known issues / limitations:
- Exact next step for the next phase:
```
