# FlightCheck rules

Rule IDs are permanent and never reused (ADR-012). Every BharatCode rule (and the robustness
rules that rest on a documented BharatCode fact) cites
<https://bharatcode.ai/build> (read 6 Nov 2026; see [BHARATCODE_CONTRACT.md](BHARATCODE_CONTRACT.md)
§15.1). Items marked **[UNVERIFIED]** stay so until the Phase 10 live probe.

Each rule has `bad_*.py` and `good_*.py` fixtures under `tests/fixtures/rules/<rule_id>/` and exact
expectations in `tests/fixtures/expected_findings.json`.

## How rules apply

- BharatCode rules run on call sites classified `bharatcode` (any confidence), or on every call
  site with `--provider bharatcode`. `--provider openai_compatible` turns them off.
- When a call site's provider confidence is `low` (receiver not resolved; repo-level provider
  used), the finding's confidence is lowered one level. `--provider bharatcode` skips this.
- Only literal values are inspected. A non-literal value (`store=flag`) never produces a contract
  violation.
- Call kinds: Responses = `responses.create|stream|parse` and raw `POST …/responses`;
  Chat = `chat.completions.create|parse|stream` and raw `POST …/chat/completions`. For raw posts
  the `json=` dict literal is the request.
- Robustness (FC-GEN) and security (FC-SEC) rules run on every call site whatever its provider;
  `--provider openai_compatible` does not turn them off, and the low-confidence downgrade does not
  apply to them (ADR-026).
- Kinds and verification: every finding here is `static` (verification `static`) except FC-BC-007,
  which is a `hypothesis`. `--fail-on` ignores hypotheses unless `--fail-on-hypotheses` is given.
- Key literals are never printed: every literal FC-SEC-001 treats as a key is masked in all
  excerpts and messages of every rule before the report is built (ADR-026).

## BharatCode contract rules (`scanner/rules/bharatcode.py`)

| ID        | Title                                      | Detection                                                                                                                                  | Severity / confidence                          | Fix                                                                                       |
| --------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------- | ----------------------------------------------------------------------------------------- |
| FC-BC-001 | `store=True` on `/responses`               | Responses call with literal `store` `True` (kwarg or `json=` key). Documented as unsupported (400).                                        | high / high                                    | Set `store=False`; replay history in `input`.                                             |
| FC-BC-002 | `previous_response_id` used                | Responses call passing `previous_response_id` with any value except literal `None`. Stateless API; documented as unsupported (400).        | high / high (literal), high / medium (variable) | Remove it; send prior turns and prior `output` items in `input`.                          |
| FC-BC-003 | Hosted (non-function) tools                | `tools` list literal (or a name bound one hop to one) containing a dict whose `type` is a string other than `"function"`.                  | high / high (literal), high / medium (one hop) | Implement the capability as your own function tool.                                       |
| FC-BC-004 | `background=True` on `/responses`          | Responses call with literal `background=True`. Background jobs are documented as unsupported (400).                                        | high / high                                    | Run synchronously or in your own worker queue.                                            |
| FC-BC-005 | Output budget above 32 000                 | Responses: literal `max_output_tokens` > 32000. Chat: literal `max_tokens` or `max_completion_tokens` > 32000.                             | medium / high                                  | Use ≤ 32 000 (includes reasoning); `reasoning={"effort": "none"}` / `reasoning_effort="none"` for short answers. |
| FC-BC-006 | Model not in the documented catalogue      | Literal `model` not in {`qwen-3.8-27b`, `deepseek-v4.1-flash`}; `deepseek-v4.1-flash` gives an `info` "requires a Pro plan" finding.       | medium / medium; info / high                   | Use `qwen-3.8-27b` or a model from `GET /models`. Catalogue as documented on 6 Nov 2026.  |
| FC-BC-007 | `store` not set to `False` on `/responses` | Responses call with no `store` and no `**kwargs` (raw posts: only when `json=` is given). Behaviour when omitted is **[UNVERIFIED]**.      | low / medium, kind `hypothesis`                | Pass `store=False` explicitly.                                                            |
| FC-BC-008 | Unsupported input modality                 | Literal (or one-hop) Responses `input` with a content item of `type` `input_audio`/`input_file`, or a `file_id`/`file_data` key. Audio, video and file input are documented as unsupported **[VERIFIED]**; exact rejected type names **[UNVERIFIED]**. | high / medium                                  | Extract text (or use image input) before calling the API.                                 |

Reserved: **FC-BC-009** (request ID never captured, P1) is not implemented yet.

## Robustness rules (`scanner/rules/robustness.py`)

Heuristic rules (001, 002, 003, 006) are lexical: they look at the attributes and strings of the
function that makes the call, or for module-level calls at the module's top-level code. They are
at most `medium` confidence and say "not observed in `<function>`" (ADR-013).

| ID         | Title                                          | Detection                                                                                                                                                                                                                         | Severity / confidence                    | Fix                                                                                                            |
| ---------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| FC-GEN-001 | Chat Completions truncation not checked        | Non-streaming `chat.completions.create` whose function reads `.message` and `.content`/`.tool_calls` but never references `finish_reason`.                                                                                       | medium / medium                          | Check `finish_reason == "length"`: keep partial output, do not run tool calls, raise the budget.               |
| FC-GEN-002 | Responses truncation not checked               | Non-streaming `responses.create` whose function reads `.output_text`/`.output` but never references `status` or `incomplete_details`.                                                                                             | medium / medium                          | Check `status == "incomplete"` (`incomplete_details.reason`).                                                  |
| FC-GEN-003 | Stream terminal events not handled             | A `for` loop over a `stream=True` call (inline, one-hop name, or `with … as stream`). Responses: the function reads `.delta`/`"response.output_text.delta"` but none of `response.completed`/`incomplete`/`failed`. Chat: the loop body never references `finish_reason`. | medium / medium                          | Handle completed, incomplete (keep partial output) and failed; treat EOF without a terminal event as failure. |
| FC-GEN-004 | No error handling around the API call          | Call site not lexically inside a `try` body in its function (`except`, `else` and `finally` blocks do not protect).                                                                                                               | medium / medium; low if the function is decorated (possible retry decorator) | Catch provider errors; bounded retry with back-off on 503; no retry on other 4xx; surface `request_id`. |
| FC-GEN-005 | Client constructed without an explicit timeout | `openai.OpenAI`/`AsyncOpenAI`, `httpx(2).Client`/`AsyncClient` without `timeout=`, and no `.with_options(timeout=…)` in the file. SDK default is 10 minutes **[VERIFIED]**; BharatCode requests may queue **[VERIFIED]**.      | low / high                               | `OpenAI(..., timeout=httpx2.Timeout(300.0, connect=10.0))` (a recommendation).                                 |
| FC-GEN-006 | Tool arguments parsed without error handling   | `json.loads(<expr>.arguments)` (or of a name bound one hop to `<expr>.arguments`) not inside a `try` catching `JSONDecodeError`, `ValueError`, `Exception` or `BaseException`.                                                   | medium / medium                          | Parse inside `try`, validate against the schema, return an error string as the tool output.                   |
| FC-GEN-007 | Tool result `output` is not a string           | Dict literal with `"type": "function_call_output"` whose `"output"` (Chat: `"role": "tool"` whose `"content"`) is a dict/list/tuple/set or non-string constant, or a bare name / call other than `json.dumps`, `str`, `repr`, `"…".format`, `"…".join`. | high / high (literal); high / low (name or call) | `"output": json.dumps(result)` with the matching `call_id`.                                       |

Reserved (P1, not implemented): **FC-GEN-008** (tool calls executed without checking completion),
**FC-GEN-009** (unbounded retry loop).

## Security rules (`scanner/rules/security.py`)

| ID         | Title                                | Detection                                                                                                                                                                                                                                               | Severity / confidence                                       | Fix                                                                       |
| ---------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| FC-SEC-001 | Hard-coded API key                   | A string literal containing `bc_live_` + ≥ 8 key characters (BharatCode prefix **[OBSERVED]**) or `sk-` + ≥ 16; or a string literal given to `api_key=`, a dict key or a name whose words end in "api key". Placeholders (`""`, `EMPTY`, `dummy`, `test`, `your_key_here`, `bc_live_your_key_here`) are info. The literal is shown only as `bc_live_…[REDACTED]` / `[REDACTED]`. | critical / high (prefix); high / medium (keyed literal); info (placeholder) | Read `BHARATCODE_API_KEY` from the environment; rotate the exposed key. |
| FC-SEC-002 | Credential written to logs or stdout | `print(…)`, `logging.<method>(…)` or `<log/logger>.<method>(…)` whose arguments reference a name/attribute whose words include api key, `secret`, `token` or `authorization`, or an environment read of such a variable (`print(f"{api_key[:4]}…")` is still flagged). | high / medium                                               | Never log credentials; log `request_id` instead.                          |

Reserved (P1, not implemented): **FC-SEC-003** (`.env` present but not ignored). FC-SEC-004 (P2)
needs non-Python scanning.

## Explicit limitations

These are copied from plan §16.2 and ADR-011. A plain-text version is shown in the HTML report's
"Limitations and mock fidelity" section (`reporting/html_report.LIMITATIONS`).

- **One-hop name resolution only.** A call's client is found from an inline constructor, a bare
  name bound by `=`, an annotated assignment or `with … as name` in the current function, then in
  enclosing functions, then at module level (class bodies are skipped), or `self.<attr>` bound
  anywhere in the same class. Clients passed as arguments, returned from factories, stored in
  containers, imported from other modules, captured by a closure inside a method, produced by
  lambdas or bound with the walrus operator are **not** resolved, nor are tuple unpacking,
  `global`/`nonlocal` or `getattr` dispatch. Such call sites are still found and take the
  repo-level provider at `low` confidence.
- **One-hop container literals** (FC-BC-003, FC-BC-008): `tools=TOOLS` is followed only to a
  list/dict/tuple literal bound to `TOOLS` in the same function, an enclosing function or the
  module, before the call in source order.
- **Only literal values are inspected.** `store=cfg.store` is unknown and never produces a
  contract violation.
- **No inter-procedural data flow**, no type inference, no evaluation of decorators, dynamic
  imports, `getattr` dispatch or framework magic. `try` coverage is lexical (the body of a `try` in
  the same function).
- **Lexical heuristics** (FC-GEN-001/002/003/006) are `medium`/`low` confidence and phrased as
  "not observed in this function". A check performed in a helper function is a false positive
  (kept on purpose as the fixture app `known_fp_helper_check`). Module-level calls use the
  module's top-level code only.
- **`try` coverage is the `try` body only.** A call in an `except`, `else` or `finally` block, or
  in a function defined inside a `try`, counts as unprotected (FC-GEN-004, FC-GEN-006). A function
  called only from inside a `try` is not analysed.
- **FC-GEN-005** does not see `OpenAI(**kwargs)`; a timeout passed that way is a false positive.
- **Static findings never claim runtime behaviour.** "Requests from this call site will …" is used
  only for documented contract rules with literal evidence; everything else uses "may".
