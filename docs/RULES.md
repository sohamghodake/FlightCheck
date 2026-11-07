# FlightCheck rules

Rule IDs are permanent and never reused (ADR-012). Every BharatCode rule cites
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
- Kinds and verification: every finding here is `static` (verification `static`) except FC-BC-007,
  which is a `hypothesis`. `--fail-on` ignores hypotheses unless `--fail-on-hypotheses` is given.

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

## Explicit limitations

These are copied from plan §16.2 and ADR-011, and will also appear in the HTML report footer.

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
- **Lexical heuristics** ("never references `finish_reason`", Phase 4) are `medium`/`low`
  confidence and phrased as "not observed in this function".
- **Static findings never claim runtime behaviour.** "Requests from this call site will …" is used
  only for documented contract rules with literal evidence; everything else uses "may".
