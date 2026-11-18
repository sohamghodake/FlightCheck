# FlightCheck rules

Rule IDs are permanent and never reused (ADR-012). Every BharatCode rule (and the robustness
rules that rest on a documented BharatCode fact) cites
<https://bharatcode.ai/build> (read 6 Nov 2026; see [BHARATCODE_CONTRACT.md](BHARATCODE_CONTRACT.md)
§15.1). Items marked **[UNVERIFIED]** stay so until the Phase 10 live probe.

Every Groq rule (`FC-GQ-*`, phase G3, ADR-037) cites a row of
[GROQ_CONTRACT.md](GROQ_CONTRACT.md) (Groq's documentation and the `groq` SDK v1.7.0, read
**18 Nov 2026**). Items marked **[UNVERIFIED]** there stay so until phase G5 verifies them live;
a Groq rule claims an HTTP status only where the contract states it (rows C2, C4, M4, S8, T3).

Each rule has `bad_*.py` and `good_*.py` fixtures under `tests/fixtures/rules/<rule_id>/` and exact
expectations in `tests/fixtures/expected_findings.json`.

## How rules apply

- Each provider rule family has a mode (ADR-025, ADR-036, ADR-037): **auto** runs it on call
  sites classified as its provider at any confidence (FC-GQ-015: on client constructors
  classified `groq`); **forced** runs it on every call site and skips the confidence
  downgrade; **disabled** never runs it. Detection decides, never a rule: a Groq model ID alone
  (GPT-OSS IDs are served elsewhere too) does not make a call site Groq.

  | Setting                                        | BharatCode rules (`FC-BC-*`) | Groq rules (`FC-GQ-*`) | Declared to detection |
  | ---------------------------------------------- | ---------------------------- | ---------------------- | --------------------- |
  | none (`--provider auto`)                       | auto                         | auto                   | —                     |
  | `--provider bharatcode`                        | forced                       | disabled               | `bharatcode`          |
  | `--provider groq`                              | disabled                     | forced                 | `groq`                |
  | `--provider openai_compatible`                 | disabled                     | disabled               | —                     |
  | `[project] provider = "bharatcode"`            | auto                         | auto                   | `bharatcode`          |
  | `[project] provider = "groq"`                  | auto                         | auto                   | `groq`                |
  | `[project] provider` of kind `openai_compatible` | disabled                   | disabled               | —                     |

  `--provider` wins over `[project] provider`; a custom profile counts by its `kind`.
- When a call site's provider confidence is `low` (receiver not resolved; repo-level provider
  used), a provider rule's finding confidence is lowered one level, except in forced mode.
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

## Groq contract rules (`scanner/rules/groq.py`)

Phase G3, ADR-037. Category `groq-contract`; every row cites docs/GROQ_CONTRACT.md, read
**18 Nov 2026**. Model facts (limits, JSON-schema support, reasoning values, tiers) come from
`core.config.GROQ_MODELS`, a catalogue dated 18 Nov 2026; messages of FC-GQ-005/006/011/014 carry
that date. Only literal values are inspected (`tools` also one hop, as FC-BC-003). A parameter
Groq documents as unsupported (FC-GQ-002, 004, 010) is still flagged when its value is a
variable, one confidence level lower (the FC-BC-002 precedent).

| ID        | Title | Detection | Severity / confidence | Fix | Contract row |
| --------- | ----- | --------- | --------------------- | --- | ------------ |
| FC-GQ-001 | `store=True` on `/responses` | Responses call with literal `store` `True`. Groq documents `store` as not supported yet, accepting only `false`/`null`; the effect of `true` is **[UNVERIFIED]** (no status claimed). | high / high | Remove it (Groq's default is `false`); replay history in `input`. | R4, R5 |
| FC-GQ-002 | `previous_response_id` used | Responses call passing `previous_response_id` with any value except literal `None`. Not stateful; the response field is always null. | high / high (literal), high / medium (variable) | Remove it; send prior turns and `output` items in `input`. | R4, R6 |
| FC-GQ-003 | Unsupported hosted or built-in tool | `tools` (literal or one hop), both endpoints: a `type` other than `function`, `mcp`, `browser_search`, `code_interpreter`; or a built-in tool (`browser_search`, `code_interpreter`) with a literal model other than `openai/gpt-oss-20b`/`-120b`. | high / high (literal), high / medium (one hop) | Use your own function tools, or a GPT-OSS model for built-in tools. | R9, T8 |
| FC-GQ-004 | Unsupported Responses parameter | Responses call with `include`, `safety_identifier`, `prompt_cache_key` or `prompt` (not `None`). Not `truncation` (R7 conflict). Rejected or ignored is **[UNVERIFIED]**. | medium / high (literal), medium / medium (variable) | Remove them. | R4 (P1) |
| FC-GQ-005 | Output budget above the model's maximum | Literal model in the catalogue and literal `max_completion_tokens`/`max_tokens` (Chat) or `max_output_tokens` (Responses) above its max completion tokens (e.g. 65 536 GPT-OSS, 32 768 Llama 3.3 70B, 16 384 Qwen 3.8). | medium / high | Stay within the model's maximum (Responses: includes reasoning). | §1.8 |
| FC-GQ-006 | Model not in Groq's catalogue, decommissioned or preview | Literal `model`: decommissioned ID (`GROQ_DECOMMISSIONED_MODELS`) → high/high ("requests return errors"); not in the catalogue → medium/medium; `llama-3.3-70b-versatile`/`llama-3.1-8b-instant` (Enterprise-only, shut down for free/developer tiers 16 Aug 2026) → low/high; preview model → info/high. | see detection | A production model from Groq's models page or `GET /models`. | §1.8, M1, M2 |
| FC-GQ-007 | Chat parameter rejected by Groq | Chat call with `logprobs`, `logit_bias` or `top_logprobs` given a literal other than `None`/`False` (whether `False` counts is **[UNVERIFIED]**). Documented 400. | high / high | Remove them. | C2 |
| FC-GQ-008 | `n` other than 1 | Chat call with a literal integer `n` ≠ 1. Documented 400. | high / high | Drop `n`; make separate requests. | C4 |
| FC-GQ-009 | Parameter with no effect on Groq | Chat call with a non-zero literal `frequency_penalty`/`presence_penalty`, or literal `metadata`. Ignored or rejected is **[UNVERIFIED]**. | low / medium | Remove them. | C5 (P1) |
| FC-GQ-010 | Deprecated Chat parameter | Chat call with `max_tokens` (use `max_completion_tokens`), `functions` (use `tools`) or `function_call` (use `tool_choice`), not `None`. | low / high (literal), low / medium (variable) | Use the replacements. | C6, C9 (P1) |
| FC-GQ-011 | Structured output on an unsupported model or mode | Literal `json_schema` format (Chat `response_format`, Responses `text.format`) with a catalogue model that lists no JSON-schema support, or `strict: True` with a best-effort-only model; or `json_schema` together with `stream=True` or a non-empty literal/one-hop `tools`. Effects **[UNVERIFIED]**. | medium / high | Use a model from S3, JSON object mode, no stream/tools. | S3, S5, T9 |
| FC-GQ-012 | Strict schema not strict-compatible | Literal `strict: True` schema in which an object (nested, `items`, `$defs`, `anyOf`…) lacks `additionalProperties: false` or leaves a property out of `required`. | medium / medium | Require every property; `additionalProperties: false`. | S2 (P1) |
| FC-GQ-013 | JSON object mode without JSON instructions | Chat call with literal `response_format={"type": "json_object"}` and a literal `messages` list in which no string mentions "json". Heuristic (ADR-013): "not observed" wording. | low / medium | Ask for JSON in the prompt. | S6 (P1) |
| FC-GQ-014 | Invalid reasoning parameters | Literal reasoning effort (Chat `reasoning_effort`, Responses `reasoning.effort`) outside the catalogue model's documented values → high/high (400); Chat `reasoning_format` on GPT-OSS, or with a literal `include_reasoning` → medium/high (status **[UNVERIFIED]**); `reasoning_format="raw"` with `json_object` mode or non-empty tools → high/high (400). | see detection | Documented efforts only; no `reasoning_format` on GPT-OSS; `parsed`/`hidden` with JSON mode or tools. | M4, M5, S8 |
| FC-GQ-015 | Wrong base URL for the client library | Client constructor: `groq.Groq`/`AsyncGroq` whose `base_url` (literal or fragments) ends in `/openai/v1` (the SDK adds it again); `openai.OpenAI`/`AsyncOpenAI` whose `base_url` names `api.groq.com` without `/openai/v1`; an `httpx`/`httpx2` client on `api.groq.com` without `/openai/v1` that posts to a path without it. | high / high | groq SDK: omit `base_url`; OpenAI SDK/raw HTTP: `https://api.groq.com/openai/v1`. | A1, K4 |
| FC-GQ-017 | `responses` called on the groq SDK | `responses.create|stream|parse` whose resolved client is `groq.Groq`/`AsyncGroq` (v1.7.0 has no `responses` resource; SDK-version dependent). | high / medium | Use the OpenAI SDK with Groq's base URL, or `chat.completions`. | R14, K8 |
| FC-GQ-018 | Too many stop sequences or tools | Chat literal `stop` list longer than 4; literal (or one-hop) `tools` list longer than 128. | medium / high (one-hop tools: medium) | At most 4 stops, 128 tools. | C7, T1 (P1) |
| FC-GQ-019 | `stream_options` without streaming | Chat call with `stream_options` (not `None`) and `stream` literal `False` or absent. A variable `stream` is not flagged (it may be `True`). | low / high | Set `stream_options` only with `stream=True`. | C10 (P1) |

Reserved, not implemented: **FC-GQ-016** (`name` on a message: contract C2 says 400, C3
contradicts it; held as a hypothesis candidate until G5 verifies it live) and **FC-GQ-020**
(`parallel_tool_calls=True` on a GPT-OSS model, P1).

## Robustness rules (`scanner/rules/robustness.py`)

Heuristic rules (001, 002, 003, 006) are lexical: they look at the attributes and strings of the
function that makes the call, or for module-level calls at the module's top-level code. They are
at most `medium` confidence and say "not observed in `<function>`" (ADR-013).

On call sites classified `groq` (FC-GEN-006/007: files whose classified call sites are all
`groq`), the message, remediation and documentation link cite Groq (ADR-037, contract §4.3):
001 `length` is a documented finish_reason (C11), budget within the model's maximum; 002
`incomplete`/`incomplete_details` (R10); 003 a chunk's `x_groq.error` ends a Chat stream (ST3;
checking only `x_groq.error` is not enough, `finish_reason` is still required), Responses
stream events undocumented (R15); 004 retry 429 after `retry-after` and 500/502/503 with
back-off, log `x_groq.id` (E2, E3, E5, E9, E10); 006 Groq's 400 `failed_generation` (T4, T6);
007 the tool message `content` is a string (T5; Responses: OpenAI convention, T10). Other call
sites keep their wording.

| ID         | Title                                          | Detection                                                                                                                                                                                                                         | Severity / confidence                    | Fix                                                                                                            |
| ---------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| FC-GEN-001 | Chat Completions truncation not checked        | Non-streaming `chat.completions.create` whose function reads `.message` and `.content`/`.tool_calls` but never references `finish_reason`.                                                                                       | medium / medium                          | Check `finish_reason == "length"`: keep partial output, do not run tool calls, raise the budget.               |
| FC-GEN-002 | Responses truncation not checked               | Non-streaming `responses.create` whose function reads `.output_text`/`.output` but never references `status` or `incomplete_details`.                                                                                             | medium / medium                          | Check `status == "incomplete"` (`incomplete_details.reason`).                                                  |
| FC-GEN-003 | Stream terminal events not handled             | A `for` loop over a `stream=True` call (inline, one-hop name, or `with … as stream`). Responses: the function reads `.delta`/`"response.output_text.delta"` but none of `response.completed`/`incomplete`/`failed`. Chat: the loop body never references `finish_reason`. | medium / medium                          | Handle completed, incomplete (keep partial output) and failed; treat EOF without a terminal event as failure. |
| FC-GEN-004 | No error handling around the API call          | Call site not lexically inside a `try` body in its function (`except`, `else` and `finally` blocks do not protect).                                                                                                               | medium / medium; low if the function is decorated (possible retry decorator) | Catch provider errors; bounded retry with back-off on 503; no retry on other 4xx; surface `request_id`. |
| FC-GEN-005 | Client constructed without an explicit timeout | `openai.OpenAI`/`AsyncOpenAI`, `groq.Groq`/`AsyncGroq`, `httpx(2).Client`/`AsyncClient` without `timeout=`, and no `.with_options(timeout=…)` in the file. The message states each library's own default: OpenAI SDK 10 minutes **[VERIFIED]** (BharatCode requests may queue **[VERIFIED]**); groq SDK 60 s total, 5 s connect (contract K5), so groq clients get only an info note (ADR-037). | low / high; info / high (groq SDK) | `OpenAI(..., timeout=httpx2.Timeout(300.0, connect=10.0))`; `Groq(..., timeout=httpx.Timeout(60.0, connect=10.0))` (recommendations). |
| FC-GEN-006 | Tool arguments parsed without error handling   | `json.loads(<expr>.arguments)` (or of a name bound one hop to `<expr>.arguments`) not inside a `try` catching `JSONDecodeError`, `ValueError`, `Exception` or `BaseException`.                                                   | medium / medium                          | Parse inside `try`, validate against the schema, return an error string as the tool output.                   |
| FC-GEN-007 | Tool result `output` is not a string           | Dict literal with `"type": "function_call_output"` whose `"output"` (Chat: `"role": "tool"` whose `"content"`) is a dict/list/tuple/set or non-string constant, or a bare name / call other than `json.dumps`, `str`, `repr`, `"…".format`, `"…".join`. | high / high (literal); high / low (name or call) | `"output": json.dumps(result)` with the matching `call_id`.                                       |

Reserved (P1, not implemented): **FC-GEN-008** (tool calls executed without checking completion),
**FC-GEN-009** (unbounded retry loop).

## Security rules (`scanner/rules/security.py`)

| ID         | Title                                | Detection                                                                                                                                                                                                                                               | Severity / confidence                                       | Fix                                                                       |
| ---------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| FC-SEC-001 | Hard-coded API key                   | A string literal containing `bc_live_` + ≥ 8 key characters (BharatCode prefix **[OBSERVED]**), `gsk_` + ≥ 8 (Groq prefix, from its placeholders, contract A4) or `sk-` + ≥ 16; or a string literal given to `api_key=`, a dict key or a name whose words end in "api key". Placeholders (`""`, `EMPTY`, `dummy`, `test`, `your_key_here`, `bc_live_your_key_here`, `gsk_your_secret_key_here`, `gsk_...`) are info. The literal is shown only as `bc_live_…[REDACTED]` / `gsk_…[REDACTED]` / `[REDACTED]`. | critical / high (prefix); high / medium (keyed literal); info (placeholder) | Read the key from the environment and rotate it: `GROQ_API_KEY` for Groq keys (prefix, a `GROQ` name or a `groq` client), `BHARATCODE_API_KEY` for BharatCode keys, the provider's variable otherwise. |
| FC-SEC-002 | Credential written to logs or stdout | `print(…)`, `logging.<method>(…)` or `<log/logger>.<method>(…)` whose arguments reference a name/attribute whose words include api key, `secret`, `token` or `authorization`, or an environment read of such a variable (`print(f"{api_key[:4]}…")` is still flagged). | high / medium                                               | Never log credentials; log `request_id` instead.                          |

Reserved (P1, not implemented): **FC-SEC-003** (`.env` present but not ignored). FC-SEC-004 (P2)
needs non-Python scanning.

## Runtime checks on Groq runs (`flightcheck test`, phase G4)

`flightcheck test` imitates one provider per run (ADR-038). A run is a **Groq run** when
`[project] provider = "groq"` (or a profile of kind `groq`) is declared, or when the
repository-level detection is `groq`; every other run imitates BharatCode exactly as before. A
Groq model ID alone never makes a run Groq (detection decides, ADR-036). The report records the
choice in `metadata.options.mock_provider`. Every Groq message cites a row of
[GROQ_CONTRACT.md](GROQ_CONTRACT.md) (read 18 Nov 2026); [UNVERIFIED] rows stay labelled.

On a Groq run the child process gets FlightCheck's fake key in `GROQ_API_KEY` and
`OPENAI_API_KEY`, the mock **root** in `GROQ_BASE_URL` (the groq SDK adds `/openai/v1`, contract
K3/K4) and `<mock>/openai/v1` in `OPENAI_BASE_URL` and any other `base_url_env` name. These
are the defaults for `base_url_env`, `api_key_env` and `scenarios` when `[test]` does not set
them; a value set in `flightcheck.toml` wins. `GROQ_API_KEY` and `GROQ_BASE_URL` are never
inherited from your shell, on any run.

Scenarios on Groq runs: S01–S06 with Groq shapes (S02's 503 bodies use Groq's error format;
S06 on a Chat stream ends with an `x_groq.error` chunk, wire format [UNVERIFIED], ST3) plus
**S11 "Rate limited (429)"**: a 429 with `retry-after: 2` and the six `x-ratelimit-*` headers
(E5, E6), then a normal answer. S11 applies to Groq runs only; selected on a BharatCode run it
gives an info diagnostic (`scenario_not_applicable`) and no result.

| Check | Meaning | BharatCode run | Groq run | Groq basis |
| ----- | ------- | -------------- | -------- | ---------- |
| FC-RT-001 | `store` is false on /responses | every scenario | — (see 009) | — |
| FC-RT-002 | No `previous_response_id` | every scenario | every scenario | R3, R4 (rejection [UNVERIFIED]) |
| FC-RT-003 | Only function tools | every scenario | — (see 015) | — |
| FC-RT-004 | No background mode | every scenario | — | R8: undocumented |
| FC-RT-005 | Supported input modalities | every scenario | — | R13: [UNVERIFIED] |
| FC-RT-006 | Output budget | every scenario (≤ 32 000) | every scenario (each model's maximum) | §1.8 (effect of exceeding [UNVERIFIED]) |
| FC-RT-007 | Model in the catalogue | every scenario (BharatCode's) | every scenario (Groq's, dated) | §1.8, C1, M1, M2, M6 |
| FC-RT-008 | Baseline run exits 0 | S01 | S01 | — |
| **FC-RT-009** | `store` is not true on /responses | — | every scenario | R5 (rejection [UNVERIFIED]) |
| FC-RT-011…014 | Tool round trip | S04 | S04 | T4, T5 (Chat); R3, T10 [UNVERIFIED] (Responses) |
| **FC-RT-015** | Only tools the model supports | — | every scenario | R9, T8 (error [UNVERIFIED]) |
| FC-RT-020 | Truncation surfaced | S03 | S03 | C11, R10 |
| FC-RT-022 | Incomplete tool call not executed | S05 | S05 | FlightCheck recommendation; T6 note |
| FC-RT-023 | No identical retry after truncation | S03, S05 | S03, S05 | FlightCheck recommendation |
| FC-RT-030 | Stream failure surfaced | S06 | S06 | ST3 ([UNVERIFIED] wire format), ST5 |
| FC-RT-040 / 041 | 503 retried / backoff present | S02 | S02 | E3, E9, K5, K6 |
| **FC-RT-044** | 429 retry waits for `retry-after` | — | S11 | E5, K6 |
| FC-RT-050 | Only the fake key was used | every scenario | every scenario | K3, K4 |
| FC-RT-060 | Request limit respected | every scenario | every scenario | — |

FC-RT-001 and FC-RT-003 keep running unchanged on BharatCode runs until G6 retires them; their
Groq counterparts have new meanings, so new IDs (ADR-012). Every-scenario checks are reported
once per run (ADR-029).

- **FC-RT-009** (high): `failed` when a /responses request sends `store` other than false or
  null (R5). An omitted `store` passes (Groq's default is false), unlike FC-RT-001.
- **FC-RT-015** (high): `failed` for a tool type Groq does not document (anything but
  `function`, `mcp`, `browser_search`, `code_interpreter`) or a built-in tool on a catalogue model
  without built-in tools (only `openai/gpt-oss-20b` / `-120b`, R9, T8); `needs_review` for a
  built-in tool with a model outside the catalogue. Both endpoints (T8: Chat too).
- **FC-RT-006 / 007 on Groq** (medium): the budget (`max_completion_tokens`, `max_tokens`,
  `max_output_tokens`) against the model's documented maximum; a model outside the catalogue
  makes FC-RT-006 `needs_review`. FC-RT-007 fails a missing model (C1), a decommissioned one
  (high, M2) or one outside the catalogue of 18 Nov 2026 (GET /models is the live list, M6);
  an Enterprise-only Llama model (shut down for free/developer tiers, M2) is `needs_review`
  (info); a preview model passes with an M1 note.
- **FC-RT-044** (medium, S11): the gap from the mock's 429 answer to the arrival of the next
  model request must be at least `retry-after` (2 s). The arrival time is recorded on Groq
  routes, so a slow answer never makes a short wait look long enough. `passed` names the SDK
  when the request carries `x-stainless-retry-count` (both SDKs honour `retry-after`, K6); no
  further request is `needs_review` (giving up may be deliberate; Groq recommends retrying with
  backoff, E9).
- **Text on Groq runs**: FC-RT-012/013/014 cite T5/T4 for Chat (verified: a Chat dict tool
  result is `failed`, a list of parts `needs_review`) and T10 [UNVERIFIED] for Responses (a
  non-string `function_call_output` is `needs_review`; a failed FC-RT-014 there mirrors at medium
  confidence). FC-RT-022 notes that Groq may answer 400 `failed_generation` instead (T6,
  [UNVERIFIED] which); FC-RT-023 is a FlightCheck recommendation and cites no Groq row;
  FC-RT-030 names the `x_groq.error` chunk; FC-RT-040/041 drop "model busy" (Groq: "wait before
  retrying", E3) and cite exponential backoff with jitter (E9). Remediation and documentation
  links come from Groq's pages (`validators.meta_for`); failed checks mirror as findings with
  `provider: "groq"`.
- **Mock on Groq routes** (`/openai/v1/...`): Groq's Chat fields (`x_groq.id`, usage timing,
  `system_fingerprint`, `service_tier`, C13/C14), error body `{"error": {"message", "type"}}`
  without `code` (E1), no `x-request-id` header (E10), the documented 400s (C2, C4, M4, S8) and,
  labelled [UNVERIFIED], 400s for `store: true`, `previous_response_id`, `include`,
  `safety_identifier`, `prompt_cache_key`, `prompt` on /responses (R4, R5, §2). Not enforced
  until G5: `messages[].name` (C2/C3 conflict), `truncation` (R7), `background` (R8), built-in
  tools on other models (R9), penalties/`metadata`/`store` on Chat (C5). Responses streams keep
  OpenAI's event shapes ([UNVERIFIED], R15); keepalive comments are never presented as Groq
  behaviour (ST4).
- **Not built (P1)**: FC-RT-045 "invalid tool call 400 handled" (T6). The mock's
  `TOOL_VALIDATION_ERROR` fault exists (400 with `failed_generation`, format [UNVERIFIED]); no
  scenario serves it yet.

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
- **Redaction is pattern- and value-based** (`core/redact.py`, ADR-033). It replaces `bc_live_`,
  `gsk_` and `sk-` keys, `Bearer` tokens, `api_key=` / `token=` / `secret=` / `password=` assignments
  (also when the quote is JSON-escaped), keys read from the environment, and exact values of
  secret-named environment variables of 8 or more characters. A secret **split across lines**
  or assembled from pieces at runtime (`"bc_live_" + tail`) is not recognised in source text;
  only its assembled value is, when it reaches an output. Short or unprefixed literals are
  masked only where a rule sees them assigned to a key-like name (ADR-026).
- **Groq rules see literal requests only.** `chat.completions.parse(response_format=Model)` and
  other SDK-built requests (Pydantic models, `extra_body`) are not inspected; a model name held in
  a variable gets no model-dependent check. Model facts are a snapshot dated 18 Nov 2026.
- **The Groq mock is a documentation model.** Groq's undocumented wire formats (the
  `x_groq.error` chunk's surroundings, Responses stream events, error `type` values other than
  `invalid_request_error`, the effect of R4 parameters) are FlightCheck's assumptions until G5
  checks them live; a runtime check that rests on one says [UNVERIFIED].
- **Static findings never claim runtime behaviour.** "Requests from this call site will …" is used
  only for documented contract rules with literal evidence; everything else uses "may".
