# Phase G1 handoff — Groq contract and migration decision

- Date / session: 2026-11-18, Claude Code chat 1 of the Groq split (G1 + G2; G2's prompt follows
  in the same chat).
- Status: done. Research and documents only; no `.py` file changed.
- What was built (files, public functions):
  - `docs/GROQ_CONTRACT.md` (new): verified facts §1 (API surface, Chat Completions, Responses
    beta, tool use, structured output, streaming, errors and limits, models dated 18 Nov 2026,
    the `groq` SDK 1.7.0, documented mistakes), "not documented" table §2, FlightCheck's own
    default Groq settings §3, migration map §4 (every FC-BC rule, FC-GEN/FC-SEC rule, FC-RT
    check, mock fault and scenario S01–S06 → keep/adapt/drop with a phase; new rules
    FC-GQ-001…020; new checks FC-RT-009/015/044/045; new scenario S11), sources with read dates.
  - `docs/DECISIONS.md`: ADR-022 entry marked void; ADR-035 "Groq replaces BharatCode"; ADR-008
    table row for `actions/upload-artifact`; ADR-032 item 5 closed.
  - `CLAUDE.md`: Groq-focused description; the provider rule now points at
    docs/GROQ_CONTRACT.md with "[UNVERIFIED] until G5"; G1–G6 prompts come from the developer
    in chat; every other rule unchanged.
  - `docs/IMPLEMENTATION_STATUS.md`: phases 10 and 12 dropped (2026-11-18); rows G1–G6; G1
    done; next phase G2.
  - `.github/workflows/flightcheck-example.yml`: `actions/upload-artifact@v6` → `@v7`.
  - Also committed first, separately: `docs: drop the code freeze` (the previous chat's
    uncommitted edits to IMPLEMENTATION_STATUS.md and PHASE_11.md, diff checked).
- Tests added (files, count) and gate results: none added (no code). Python 3.12 (macOS):
  `ruff check .` pass, `ruff format --check .` pass (209 files), `pytest -m "not live" -q`
  1445 passed, `python scripts/eval_fixtures.py --check --runtime` all pass (273/273, 0
  mismatches), `flightcheck scan src --fail-on low` exit 0.
- Decisions made (ADR ids): ADR-035 (strategy: add Groq first, keep BharatCode green until G6;
  `FC-GQ-*` IDs; schema 1.0 additive now, G6 chooses legacy enum value vs 2.0 with (a)
  recommended; list of ADRs G6 is expected to supersede), ADR-022 void.
- Deviations from the plan and why:
  - Docs were fetched as each page's Markdown rendering (`<page>.md`, served by Groq) with
    `curl`, not through a browser or web-fetch agent: same official pages, exact text, fewer
    tokens. The SDK was read from GitHub at tag `v1.7.0` (raw files and the contents API).
  - `actions/upload-artifact` was read through the GitHub releases API (same data as the
    releases page).
  - The research found contradictions inside Groq's own docs (`messages[].name` vs the tool
    example; Responses `truncation`; strict-mode model list; rate-limit tables; Responses
    "stateful"). Each is recorded as [UNVERIFIED] with both sources instead of picking one.
- Out-of-scope changes (file, reason, test): none. (The workflow pin is the side task the
  prompt asked for.)
- Known issues / limitations:
  - Many Groq "unsupported" items do not say what happens (400 or ignored); rules must word
    them as "documented as unsupported" until G5 (contract §2).
  - The model catalogue changes often (Llama models became Enterprise-only in Aug 2026); the
    snapshot is dated 18 Nov 2026.
  - Free-tier rate-limit numbers conflict between two Groq pages (contract E7).
  - Found, not fixed (noted in contract §4.9): FC-GEN-005's "10 minutes" default applies to the
    OpenAI SDK only (groq: 60 s) → G3; the runtime harness does not scrub/set `GROQ_API_KEY`
    and `GROQ_BASE_URL` → G4.
  - Phase 11 and this phase are committed locally, not pushed.
- Exact next step for the next phase: G2 — add the `groq` provider value (additive, ADR-010),
  Groq detection signals in `scanner/detect.py` (host `api.groq.com`, `GROQ` env names,
  `groq.Groq`/`AsyncGroq`, documented model IDs as supporting signals) with a new ADR for the
  tie-break order, Groq constants and the dated catalogue in `core/config.py`,
  `GROQ_PROFILE` in `providers/capabilities.py` (decide how model-dependent capabilities are
  expressed), and `gsk_` / `GROQ_API_KEY` redaction in `core/redact.py`; keep every BharatCode
  gate green. Source: docs/GROQ_CONTRACT.md §1 and §4 (items marked G2).
