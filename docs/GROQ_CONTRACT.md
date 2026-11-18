# Groq API contract

This file is FlightCheck's reference for what Groq's API documents, and what it does not. From
phase G2 on, every Groq rule, mock behaviour and adapter check must trace back to a row here. It
replaces [BHARATCODE_CONTRACT.md](BHARATCODE_CONTRACT.md) as the provider contract (ADR-035);
that file stays, unchanged, until phase G6 removes the BharatCode path.

- Read on **18 November 2026** (Asia/Kolkata) from Groq's official documentation
  (`console.groq.com/docs/…`, fetched as the Markdown rendering each page offers at
  `<page>.md`), the `groq` Python SDK (GitHub `groq/groq-python`, tag `v1.7.0`) and its PyPI JSON.
  No blog, forum or memory was used. Groq's docs change often: re-read before relying on a row
  that is more than a few weeks old.
- Tags: **[VERIFIED]** the official source says it plainly · **[UNVERIFIED]** missing, unclear,
  contradicted elsewhere in the docs, or only checkable by a live call; phase **G5** verifies
  these live and replaces the tag with a dated result · **[ASSUMPTION]** planning assumption ·
  **[RECOMMENDATION]** FlightCheck's own design choice (an ADR may revise it).
- "Unsupported" in Groq's docs does not always say what happens. Where the docs name the HTTP
  status, the row says so; otherwise the rejection behaviour is [UNVERIFIED] and rules must not
  claim a status code.
- Rule IDs (`FC-GQ-*`, `FC-GEN-*`, `FC-SEC-*`), check IDs (`FC-RT-*`) and scenarios (`S01`…) are
  FlightCheck's own identifiers.

Source keys used in the tables (full URLs in [Sources](#sources)):
`openai` OpenAI compatibility · `api` API reference · `resp` Responses API · `chat` text
generation · `so` structured outputs · `tools` tool-use overview · `local` local tool calling ·
`builtin` built-in tools · `reason` reasoning · `errors` error codes · `limits` rate limits ·
`models` supported models · `depr` deprecations · `libs` client libraries · `quick` quickstart ·
`sec` security onboarding · `check` production-ready checklist · `tiers` service tiers · `flex`
flex processing · `perms` model permissions · `sdk` groq-python v1.7.0 source · `pypi` PyPI JSON.

---

## 1. Verified facts

### 1.1 API surface (research item 1)

| #   | Fact                                                                                                                                                                                                                       | Source              | Tag          |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- | ------------ |
| A1  | OpenAI-compatible base URL: `https://api.groq.com/openai/v1`. Groq calls the API "mostly compatible" with OpenAI's client libraries, not fully.                                                                            | openai, api         | VERIFIED     |
| A2  | Authentication: `Authorization: Bearer <key>` on every request.                                                                                                                                                           | api, models         | VERIFIED     |
| A3  | Key environment variable: `GROQ_API_KEY` (used in every example; the SDK reads it by default). Keys are created at `console.groq.com/keys`.                                                                                | quick, libs, sdk    | VERIFIED     |
| A4  | Key prefix: the docs' placeholders are `gsk_your_secret_key_here` and `gsk_...`. The format itself is not specified.                                                                                                      | sec                 | UNVERIFIED (placeholder only; good enough for redaction) |
| A5  | Endpoints under the base URL: `POST /chat/completions`, `POST /responses` (beta), `GET /models`, `GET /models/{model}`, audio (`/audio/transcriptions`, `/audio/translations`, `/audio/speech`), `/files`, `/batches`. No `/completions` (legacy text) endpoint is listed. | api                 | VERIFIED     |
| A6  | `GET /models` returns a JSON list of all active models. The field-level response shape is not shown in the pages read.                                                     | models              | VERIFIED (list) · UNVERIFIED (shape) |
| A7  | Model access can be restricted per organisation/project; a request for a restricted model returns **403**.                                                                                                                | perms               | VERIFIED     |
| A8  | Groq recommends its own `groq` Python and TypeScript libraries but documents the OpenAI SDK with `base_url` as supported.                                                                                                 | openai, libs        | VERIFIED     |
| A9  | Keys must stay server-side: never hard-code them, never ship them in front-end bundles; use environment variables or a secret manager; never disable TLS verification.                                                    | sec                 | VERIFIED     |

### 1.2 Chat Completions (research item 2)

| #   | Fact                                                                                                                                                                                                                       | Source        | Tag      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | -------- |
| C1  | Required: `messages`, `model`.                                                                                                                                                                                             | api           | VERIFIED |
| C2  | Not supported and **400** if supplied: `logprobs`, `logit_bias`, `top_logprobs`, `messages[].name`.                                                                                                                        | openai        | VERIFIED (but see C3) |
| C3  | Contradiction: Groq's own local-tool-calling example sends `"name"` on `role: "tool"` messages, which C2 says is a 400. Which message roles C2 covers is unclear.                                                         | local, openai | UNVERIFIED (G5) |
| C4  | `n` must be 1; any other value is a 400.                                                                                                                                                                                   | openai, api   | VERIFIED |
| C5  | Described as not yet supported by any model: `frequency_penalty`, `presence_penalty` (and `logprobs`, `logit_bias`, `top_logprobs`). `metadata` and `store` are described as not currently supported. Whether the penalties/`metadata`/`store` are rejected or ignored is not stated. | api           | VERIFIED (unsupported) · UNVERIFIED (effect) |
| C6  | `max_tokens` is **deprecated** in favour of `max_completion_tokens`. Input + generated tokens are limited by the model's context length.                                                                                    | api           | VERIFIED |
| C7  | `stop`: string or array, **up to 4** sequences; the stop sequence is not returned.                                                                                                                                         | api, chat     | VERIFIED |
| C8  | `temperature` 0–2 (default 1); a value of 0 is converted to `1e-8`. `top_p` 0–1.                                                                                                                                           | openai, api   | VERIFIED |
| C9  | `functions` / `function_call` are deprecated in favour of `tools` / `tool_choice`.                                                                                                                                         | api           | VERIFIED |
| C10 | `stream_options` should only be set together with `stream: true`.                                                                                                                                                         | api           | VERIFIED |
| C11 | `finish_reason` values (non-streaming): `stop`, `length`, `tool_calls`, `function_call`; streaming chunks may also carry `content_filter`. Groq's prose pages document only `stop` and `tool_calls`.                       | sdk, local    | VERIFIED (SDK types, generated from Groq's OpenAPI spec) |
| C12 | What `length` means (output budget exhausted) and how to handle it is not described by Groq.                                                                                                                              | —             | UNVERIFIED (OpenAI meaning assumed) |
| C13 | Groq-specific response fields: `x_groq` (with `id`, a request ID such as `req_…`, and optional `usage`/`debug`), `usage_breakdown`, `mcp_list_tools`, `service_tier`, `system_fingerprint`; `usage` adds `queue_time`, `prompt_time`, `completion_time`, `total_time`. | api, sdk      | VERIFIED |
| C14 | Groq-specific request fields: `service_tier` (`auto`, `on_demand` default, `flex`, `performance`), `reasoning_effort`, `reasoning_format`, `include_reasoning`, `disable_tool_validation`, `documents`, `citation_options`, `search_settings`, `compound_custom`; `exclude_domains`/`include_domains` are deprecated. | api, tiers    | VERIFIED |

### 1.3 Responses API (research item 3)

| #   | Fact                                                                                                                                                                                                                       | Source    | Tag      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- | -------- |
| R1  | `POST /responses` exists and is **in beta**.                                                                                                                                                                               | resp, api | VERIFIED |
| R2  | Text and image input, text output; function calling; structured output through `text.format` (`type: "json_schema"`, `name`, `schema`); reasoning through `reasoning: {effort}`; `responses.parse` with the OpenAI SDK.   | resp      | VERIFIED |
| R3  | Not stateful: history must be replayed in `input` on every request (the page's intro sentence about "stateful conversations" contradicts this; the multi-turn section and the API reference win).                         | resp, api | VERIFIED |
| R4  | Listed as not supported yet: `previous_response_id`, `store`, `truncation`, `include`, `safety_identifier`, `prompt_cache_key`, `prompt` (reusable prompts). The effect of sending them is not stated.                | resp      | VERIFIED (unsupported) · UNVERIFIED (effect) |
| R5  | `store` accepts only `false` or `null`; default `false`. So `store: false` is accepted and omitting `store` is fine.                                                                                     | api       | VERIFIED |
| R6  | `previous_response_id` in the response object: "Not supported. Always null."                                                                                                                                              | api       | VERIFIED |
| R7  | Contradiction: the API reference lists `truncation` as a request field with `auto`/`disabled`; the Responses page lists it as unsupported.                                                                                 | api, resp | UNVERIFIED (G5) |
| R8  | `background` is not a documented request field (it appears only in the response object). Background mode is therefore undocumented.                                                                                       | api       | UNVERIFIED |
| R9  | Hosted/built-in tools on `/responses`: `browser_search` and `code_interpreter` (with `container: {type: "auto"}`) on `openai/gpt-oss-20b` and `openai/gpt-oss-120b` only; remote MCP tools (`type: "mcp"`). The API reference's "function definitions only" contradicts this; the dedicated pages win. | resp, api | VERIFIED (supported models) · UNVERIFIED (error on other models) |
| R10 | `status` values: `completed`, `failed`, `in_progress`, `incomplete`; `incomplete_details` and `error` fields exist. `max_output_tokens` includes reasoning tokens.                                                         | api       | VERIFIED |
| R11 | Reasoning output appears in `output` as items of type `reasoning` with `reasoning_text` content.                                                                                                                          | resp      | VERIFIED |
| R12 | Header `Groq-Beta: inference-metrics` adds timing keys to `metadata`.                                                                                                                                                      | resp      | VERIFIED |
| R13 | Audio, video or file input on `/responses`: only text and image input are claimed; rejection of other modalities is not stated.                                                                                       | resp      | UNVERIFIED |
| R14 | The `groq` Python SDK v1.7.0 has **no `responses` resource** (resources: `audio`, `batches`, `chat`, `embeddings`, `files`, `models`). Responses calls need the OpenAI SDK or raw HTTP.                                   | sdk       | VERIFIED (v1.7.0) |
| R15 | Responses streaming event names and payloads are not documented by Groq.                                                                                                                                                   | —         | UNVERIFIED |

### 1.4 Tool use (research item 4)

| #   | Fact                                                                                                                                                                                                                       | Source           | Tag      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | -------- |
| T1  | Function tools: `tools` entries `{type: "function", function: {name, description, parameters}}`; at most **128** functions per request (both endpoints). Groq advises 3–5 tools, 10–15 at most.                         | api, local       | VERIFIED |
| T2  | `tool_choice`: `none` (default without tools), `auto` (default with tools), `required`, or `{type: "function", function: {name}}`.                                                                                        | api, local       | VERIFIED |
| T3  | `required` or a named function: if the model does not comply the API returns **400**. `none`: some models may still try to call a tool, which also returns **400**.                                                      | local            | VERIFIED |
| T4  | Tool calls: `finish_reason: "tool_calls"`; each call has `id`, `type: "function"`, `function.name`, `function.arguments` (a JSON **string**).                                                                               | tools, local     | VERIFIED |
| T5  | Tool result: a `role: "tool"` message whose `tool_call_id` must match the call's `id`; `content` is a string ("any string value"). Groq recommends returning structured data via `json.dumps`.                            | tools, local     | VERIFIED |
| T6  | Groq validates generated tool calls: an invalid one returns **400** with `error.failed_generation` (reason, `tool_call_id`, attempted arguments). `disable_tool_validation: true` skips the check that the tool exists in `tools`. | local, api       | VERIFIED |
| T7  | `parallel_tool_calls` (default `true`). Parallel tool use per model: **no** for `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `openai/gpt-oss-safeguard-20b`; yes for `qwen/qwen3.8-27b`, `minimaxai/minimax-m2.7`, `meta-models/muse-spark-open`, `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`. | tools, api       | VERIFIED |
| T8  | Built-in tools (executed on Groq's servers) `browser_search`, `code_interpreter`: `openai/gpt-oss-20b`/`-120b` only, on Chat Completions too (`tools=[{"type": "browser_search"}]`). Not available on regional endpoints. | builtin, tools   | VERIFIED |
| T9  | Tool use is not supported together with Structured Outputs.                                                                                                                                                                | so               | VERIFIED |
| T10 | Responses-API tool continuation (`function_call` / `function_call_output` items, `call_id`) is not described by Groq beyond "fully compatible with OpenAI's Responses API".                                              | resp             | UNVERIFIED |

### 1.5 Structured output (research item 5)

| #   | Fact                                                                                                                                                                                                                       | Source   | Tag      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | -------- |
| S1  | Chat: `response_format: {type: "json_schema", json_schema: {name, schema, strict}}` or `{type: "json_object"}`. Responses: `text.format` with `type: "json_schema"`.                                                      | api, so, resp | VERIFIED |
| S2  | `strict` defaults to `false` (best effort). `strict: true` uses constrained decoding and requires every property in `required` and `additionalProperties: false`; optional fields are not supported in strict mode.       | so       | VERIFIED |
| S3  | `strict: true` models: `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `meta-models/muse-spark-open`. Best-effort models: those four plus `openai/gpt-oss-safeguard-20b`. Other models: JSON object mode only. (The page's comparison table still says strict is "GPT-OSS 20B, 120B" only; the model list wins.) | so       | VERIFIED |
| S4  | Best-effort mode may return **400** with the message "Generated JSON does not match the expected schema. Please adjust your prompt."                                                                                      | so       | VERIFIED |
| S5  | Streaming and tool use are not supported with Structured Outputs.                                                                                                                                                          | so       | VERIFIED (unsupported) · UNVERIFIED (effect) |
| S6  | JSON object mode works on all models, needs explicit JSON instructions in the prompt, and either returns valid JSON or an error.                                                                                         | so       | VERIFIED |
| S7  | What happens when `json_schema` is sent for a model outside S3.                                                                                                                                                           | —        | UNVERIFIED |
| S8  | `reasoning_format: "raw"` together with JSON mode or tool use returns **400** (the default switches to `parsed`/`hidden` in those modes).                                                                                | reason   | VERIFIED |

### 1.6 Streaming (research item 6)

| #   | Fact                                                                                                                                                                                                                       | Source  | Tag      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | -------- |
| ST1 | Chat `stream: true` sends data-only server-sent events and ends with `data: [DONE]`.                                                                                                                                       | api     | VERIFIED |
| ST2 | Usage in streams: the final chunk carries `x_groq.usage`; `stream_options: {include_usage: true}` also yields a top-level `usage` on the last chunk. `x_groq.id` is sent in the first and final chunk.                  | sdk     | VERIFIED (SDK types) |
| ST3 | Mid-stream errors: chunks carry `x_groq.error`, "an error string indicating why a stream was stopped early". Whether an SSE `error` event is also sent, and what the HTTP status is, is not documented.                | sdk     | VERIFIED (field) · UNVERIFIED (wire format) |
| ST4 | Keepalive/comment lines, and Responses streaming events (R15), are not documented.                                                                                                                                         | —       | UNVERIFIED |
| ST5 | Groq's checklist asks apps to configure timeouts for streaming and "handle streaming errors gracefully".                                                                                                                  | check   | VERIFIED |

### 1.7 Errors and limits (research item 7)

| #   | Fact                                                                                                                                                                                                                       | Source        | Tag      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | -------- |
| E1  | Error body: `{"error": {"message": "...", "type": "invalid_request_error"}}`. Tool-validation errors add `failed_generation` (T6). No `code` field is documented.                                                         | errors, local | VERIFIED |
| E2  | Documented statuses: 400, 401, 403, 404, **413** (body too large), **422** (semantic errors or "model hallucinations"; retry possible), **424** (failed dependency, e.g. remote MCP auth), 429, **498** (flex tier at capacity; error `capacity_exceeded`), **499** (request cancelled, logs only), 500, 502, 503; 206 informational. | errors, flex  | VERIFIED |
| E3  | Server errors (5xx) are not charged. 500: retry later; 502: retrying may help; 503: "wait before retrying".                                                                                                               | errors        | VERIFIED |
| E4  | Rate limits are per organisation in RPM, RPD, TPM, TPD (plus ASH/ASD for audio, and optionally ITPM/OTPM); whichever is hit first applies. Cached tokens do not count.                                                   | limits        | VERIFIED |
| E5  | Exceeding a limit returns **429**; `retry-after` (seconds) is set **only** on 429.                                                                                                                                         | limits        | VERIFIED |
| E6  | Always-present headers: `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests` (all **per day**, RPD) and `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens`, `x-ratelimit-reset-tokens` (all **per minute**, TPM). Reset values look like `2m59.56s`, `7.66s`. | limits        | VERIFIED |
| E7  | Plan limits: the rate-limits page shows, for example, `openai/gpt-oss-20b`/`-120b` and `qwen/qwen3.8-27b` at 30 RPM, 1K RPD, 8K TPM, 200K TPD and calls them "base limits for the Developer plan", while the models page lists Developer-plan limits of 250K TPM / 1K RPM for the same models. The exact free-tier numbers are therefore unclear; the account's limits page is authoritative. | limits, models | UNVERIFIED (conflict) |
| E8  | Request size limit: 413 exists; no byte limit is documented for chat requests. Per-model "max file size" (e.g. 20 MB for `qwen/qwen3.8-27b`, 100 MB for Whisper) is listed.                                               | errors, models | VERIFIED (413, file sizes) · UNVERIFIED (body limit) |
| E9  | Groq's security guide recommends client-side rate limiting and exponential backoff with jitter for 429/5xx (its example caps a wait at 30 s and tries 5 times).                                                         | sec           | VERIFIED |
| E10 | Request ID: returned in the body as `x_groq.id`. An `x-request-id` response header is not documented.                                                                                                                     | api, sdk      | VERIFIED (body) · UNVERIFIED (header) |

### 1.8 Models — as listed on 18 November 2026 (research item 8)

This changes often; FC-GQ-006 must carry this date.

| Model ID                              | Tier on 18 Nov 2026            | Context | Max completion tokens | Tools / parallel | Strict JSON schema | Reasoning (`reasoning_effort`)                       | Built-in tools |
| ------------------------------------- | ------------------------------ | ------- | --------------------- | ---------------- | ------------------ | ---------------------------------------------------- | -------------- |
| `openai/gpt-oss-120b`                 | production                     | 131,072 | 65,536                | yes / no         | yes                | `low`, `medium` (default), `high`; `reasoning_format` not supported | yes |
| `openai/gpt-oss-20b`                  | production                     | 131,072 | 65,536                | yes / no         | yes                | as above                                             | yes            |
| `llama-3.3-70b-versatile`             | production, Enterprise only (deprecated for free/developer 16 Aug 2026) | 131,072 | 32,768 | yes / yes  | no (JSON object)   | —                                                    | no             |
| `llama-3.1-8b-instant`                | production, Enterprise only (deprecated for free/developer 16 Aug 2026) | 131,072 | 131,072 | yes / yes | no (JSON object)   | —                                                    | no             |
| `qwen/qwen3.8-27b`                    | preview                        | 131,072 | 16,384                | yes / yes        | yes                | `none` (default per API ref), `low`, `medium`, `high` (see M3) | no      |
| `openai/gpt-oss-safeguard-20b`        | preview                        | 131,072 | 65,536                | yes / no         | best effort only   | supported (values not listed)                        | no             |
| `meta-models/muse-spark-open`         | preview, Enterprise            | 131,072 | 131,072               | yes / yes        | yes                | `low`, `medium`, `high`, `xhigh` (default)           | no             |
| `minimaxai/minimax-m2.7`              | preview, Enterprise            | 196,608 | 131,072               | yes / yes        | no                 | supported                                            | no             |
| `meta-llama/llama-prompt-guard-2-22m`, `meta-llama/llama-prompt-guard-2-86m` | preview (classifiers) | 512     | 512                   | —                | —                  | —                                                    | —              |
| `whisper-large-v3`, `whisper-large-v3-turbo` | production (audio)      | —       | —                     | —                | —                  | —                                                    | —              |
| `canopylabs/orpheus-v1-english`, `canopylabs/orpheus-arabic-saudi` | preview (TTS) | 4,000 | 50,000          | —                | —                  | —                                                    | —              |

| #  | Fact                                                                                                                                                                                                                         | Source         | Tag      |
| -- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- | -------- |
| M1 | Production models are meant for production; preview models are for evaluation only and may be discontinued at short notice.                                                                                     | models, depr   | VERIFIED |
| M2 | Decommissioned IDs fail after their shutdown date (e.g. `groq/compound`, `groq/compound-mini` on 21 Sep 2026: "requests to these model IDs will return errors"). Recent shutdowns: `qwen/qwen3.6-27b` (14 Sep 2026 → `qwen/qwen3.8-27b`), `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` (16 Aug 2026, free/developer tiers only), `qwen/qwen3-32b`, `meta-llama/llama-4-scout-17b-16e-instruct` (17 Jul 2026), `moonshotai/kimi-k2-instruct-0905` (15 Apr 2026), `meta-llama/llama-4-maverick-17b-128e-instruct` (9 Mar 2026), `meta-llama/llama-guard-4-12b` (5 Mar 2026), and older IDs listed on the deprecations page (`gemma2-9b-it`, `llama3-70b-8192`, `llama3-8b-8192`, `mixtral-8x7b-32768`, `deepseek-r1-distill-llama-70b`, `qwen-qwq-32b`, `mistral-saba-24b`, `moonshotai/kimi-k2-instruct`, …). | depr           | VERIFIED |
| M3 | `qwen/qwen3.8-27b` default reasoning: the API reference says the default is `none`; the reasoning page says `default` returns no reasoning tokens. Consistent in effect, worded differently.                             | api, reason    | VERIFIED (no reasoning by default) |
| M4 | `reasoning_effort` values outside a model's supported set are rejected with **400**. GPT-OSS models do not accept `none`.                                                                                                  | api            | VERIFIED |
| M5 | `reasoning_format` (`hidden`, `raw`, `parsed`) and `include_reasoning` are mutually exclusive; `reasoning_format` is not supported on GPT-OSS models. The status code for either misuse is not stated.                     | reason, api    | VERIFIED (rule) · UNVERIFIED (status) |
| M6 | `GET /models` is the live source of active model IDs; FlightCheck's catalogue (this table) is a dated snapshot.                                                                             | models         | VERIFIED |

### 1.9 The `groq` Python SDK (research item 9)

| #   | Fact                                                                                                                                                                                                                   | Source     | Tag      |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------- |
| K1  | Latest release **1.7.0**, uploaded 2026-08-26; `Requires-Python >=3.10`. (The libraries page still says "Python 3.7+"; PyPI wins.)                                                                                    | pypi, libs | VERIFIED |
| K2  | Clients `groq.Groq` and `groq.AsyncGroq`, built on `httpx` (not `httpx2`).                                                                                                                                             | sdk        | VERIFIED |
| K3  | Environment variables read: `GROQ_API_KEY` (key; error if missing), `GROQ_BASE_URL` (base URL), `GROQ_CUSTOM_HEADERS`.                                                                                                | sdk        | VERIFIED |
| K4  | **Default base URL is `https://api.groq.com`** (no `/openai/v1`); resource paths include the prefix (`/openai/v1/chat/completions`). So `Groq(base_url="https://api.groq.com/openai/v1")` would request `/openai/v1/openai/v1/…`. The OpenAI SDK, in contrast, needs the full `…/openai/v1` base URL. | sdk, openai | VERIFIED |
| K5  | Default timeout **60 s** total, **5 s** connect; default **2 retries**; back-off 0.5 s initial, 8 s max.                                                                                                               | sdk        | VERIFIED |
| K6  | Retries on 408, 409, 429 and ≥ 500, or when `x-should-retry: true`; never when `x-should-retry: false`. Honours `retry-after-ms` and `retry-after`. Sends `x-stainless-retry-count` on every request.                 | sdk        | VERIFIED |
| K7  | Exceptions: `GroqError` → `APIError` → `APIStatusError` (`BadRequestError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `ConflictError`, `UnprocessableEntityError`, `RateLimitError`, `InternalServerError`) and `APIConnectionError` → `APITimeoutError`. | sdk        | VERIFIED |
| K8  | No `responses` resource (R14).                                                                                                                                                                                          | sdk        | VERIFIED |

### 1.10 Integration mistakes Groq documents (research item 10)

| #   | Mistake (as Groq describes it)                                                                                     | Source         | Tag      |
| --- | ------------------------------------------------------------------------------------------------------------------ | -------------- | -------- |
| X1  | Hard-coding keys or committing them; exposing them in browser bundles.                                             | sec, libs      | VERIFIED |
| X2  | Sending `logprobs`, `logit_bias`, `top_logprobs`, `messages[].name`, or `n` ≠ 1 (400).                            | openai         | VERIFIED |
| X3  | Forcing tools (`required`, named function) without steering the prompt (400 when the model does not comply).      | local          | VERIFIED |
| X4  | Using structured outputs with streaming or tools; `strict: true` schemas with optional fields or without `additionalProperties: false`; JSON object mode without JSON instructions in the prompt. | so | VERIFIED |
| X5  | Not retrying best-effort schema 400s / invalid tool calls; not validating tool arguments; not returning tool errors to the model. | so, local      | VERIFIED |
| X6  | No client-side backoff for 429/5xx; ignoring rate-limit headers.                                                   | sec, limits    | VERIFIED |
| X7  | Using a preview model in production; not migrating before a model's shutdown date.                                 | models, depr   | VERIFIED |
| X8  | `reasoning_format: "raw"` with JSON mode or tools (400); mixing `reasoning_format` and `include_reasoning`.        | reason         | VERIFIED |
| X9  | Using `max_tokens` (deprecated) and a default 1024-token completion budget too low for reasoning models.           | api, reason    | VERIFIED |
| X10 | Using flex without retries (498 `capacity_exceeded` fails fast).                                                    | flex, check    | VERIFIED |

---

## 2. Not documented (or contradicted) — G5 verifies live

| Item                                                                                                  | Status       | Handling until G5                                                                     |
| ----------------------------------------------------------------------------------------------------- | ------------ | ------------------------------------------------------------------------------------- |
| Effect of `store: true`, `previous_response_id`, `include`, `safety_identifier`, `prompt_cache_key`, `prompt` on `/responses` (400 or ignored) | UNVERIFIED | Rules say "documented as unsupported", never "returns 400"; the mock rejects with 400 and labels it `[UNVERIFIED]` |
| `truncation` on `/responses` (R7 conflict)                                                            | UNVERIFIED   | No rule                                                                               |
| `background` on `/responses` (R8)                                                                     | UNVERIFIED   | No rule; capability `unknown`                                                         |
| `messages[].name` on `role: "tool"` / `assistant` messages (C3 conflict)                              | UNVERIFIED   | Candidate rule FC-GQ-016 held back until G5                                            |
| Penalties, `metadata`, `store` on Chat: rejected or ignored (C5)                                     | UNVERIFIED   | Low-severity "has no effect on Groq" wording                                          |
| `json_schema` on a model outside S3 (S7); structured outputs with `stream`/`tools` (S5)               | UNVERIFIED   | Medium severity; no status claimed                                                     |
| Built-in/hosted tools on a non-GPT-OSS model (R9)                                                     | UNVERIFIED   | High severity "not supported on this model"; no status claimed                        |
| Audio/video/file input on `/responses` (R13)                                                         | UNVERIFIED   | No rule                                                                               |
| Responses streaming events (R15); Chat mid-stream error wire format (ST3); keepalive comments (ST4)  | UNVERIFIED   | Mock follows OpenAI shapes plus Groq's `x_groq.error` chunk field                      |
| Responses tool continuation (`function_call_output`, `call_id`) (T10)                                 | UNVERIFIED   | OpenAI convention assumed                                                              |
| `GET /models` field shape (A6)                                                                        | UNVERIFIED   | OpenAI `{"object": "list", "data": [...]}` assumed                                    |
| Behaviour when `max_completion_tokens` exceeds the model's maximum                                   | UNVERIFIED   | Medium severity; adapter clamps                                                       |
| `x-request-id` response header (E10)                                                                  | UNVERIFIED   | Use `x_groq.id` from the body                                                          |
| Free-tier rate-limit numbers (E7 conflict); chat request body size limit (E8)                         | UNVERIFIED   | Not checked                                                                           |
| Error `code` values (e.g. for an unknown or decommissioned model)                                    | UNVERIFIED   | Map by status first, `type`/`message` second                                          |
| Key format beyond the `gsk_` placeholder (A4)                                                         | UNVERIFIED   | Redact `gsk_` + ≥ 8 key characters                                                    |

---

## 3. Default settings for FlightCheck's own Groq calls (`explain`, G5)

| Setting                    | Value                                                                                                      | Source                                     |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| Transport                  | Raw HTTP through `providers/http.py` (ADR-003, ADR-004); no `groq` dependency                             | [RECOMMENDATION] (no new dependency, ADR rule) |
| `base_url`                 | `https://api.groq.com/openai/v1`                                                                           | [VERIFIED] A1                              |
| Key env var                | `GROQ_API_KEY`                                                                                             | [VERIFIED] A3                              |
| Default model              | `openai/gpt-oss-20b` (production; strict JSON schema; cheapest GPT-OSS)                                   | [VERIFIED] §1.8 on 18 Nov 2026             |
| Endpoint                   | `POST /chat/completions` (stable; `/responses` is beta and the `groq` SDK lacks it)                       | [RECOMMENDATION] based on R1, R14          |
| Output format              | `response_format: {type: "json_schema", json_schema: {name, strict: true, schema}}`, schema with every field required and `additionalProperties: false`; no `stream`, no `tools` | [VERIFIED] S1–S3, S5, T9 |
| `max_completion_tokens`    | 4 000 (configurable; hard cap 65 536 for GPT-OSS)                                                          | [ASSUMPTION] adequate for 1–5 findings; cap [VERIFIED] |
| Reasoning                  | `reasoning_effort: "low"`, `include_reasoning: false`                                                      | [VERIFIED] values exist for GPT-OSS (M4, M5) |
| `service_tier`             | omitted (`on_demand`)                                                                                      | [VERIFIED] C14                             |
| Timeouts                   | connect 10 s, read 60 s, write 30 s, pool 10 s                                                             | [RECOMMENDATION] (SDK default 60 s / 5 s, K5) |
| Retries                    | up to 2 retries on 429 (wait `retry-after`, capped at 30 s), 500, 502, 503 and connection errors; exponential back-off with jitter from 1 s; never on other 4xx | [RECOMMENDATION] from E3, E5, E9 |
| Request ID                 | record `x_groq.id` from the body (and `x-request-id` if present)                                          | [VERIFIED] E10 (body)                      |

---

## 4. Migration map (BharatCode → Groq)

Phases: **G2** detection, provider profile, constants, redaction · **G3** static rules · **G4** mock,
scenarios, runtime checks · **G5** adapter, `explain`, live verification · **G6** removal of the
BharatCode path, docs cleanup.

### 4.1 Rule ID scheme

- New family **`FC-GQ-NNN`** for Groq contract rules, numbered from 001 in the order below.
  Numbers do not mirror `FC-BC-*`: `FC-GQ-004` is not "background".
- **`FC-GEN-*`** and **`FC-SEC-*`** are provider-neutral and keep their IDs and meanings; only
  citations, wording and remediation text change (ADR-012 allows that; a change of *meaning*
  would need a new ID).
- **`FC-BC-001…008`** run unchanged until G6, then become **retired** (listed as such in
  docs/RULES.md, IDs never reused, ADR-012). `FC-BC-009` stays reserved and retires with them.
- **`FC-RT-*`** checks are already neutral in ID; checks whose meaning is BharatCode-only retire
  in G6, adapted checks keep their ID where the meaning is unchanged and take a new number
  where it changes (e.g. the budget limit becomes per-model: same meaning "budget within the
  provider's documented maximum", so the ID stays).
- Provider value **`groq`** joins the `Provider` literal, `AIExplanation.backend` and
  `--provider` (additive, ADR-010).

### 4.2 Existing BharatCode rules

| Existing  | Decision | Groq meaning / reason                                                                                                                                                         | Phase |
| --------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| FC-BC-001 `store=True` | adapt → **FC-GQ-001** | `store` only accepts `false`/`null` on Groq `/responses` (R4, R5). Severity high / confidence high; message "documented as unsupported", no status claim until G5. | G3 |
| FC-BC-002 `previous_response_id` | adapt → **FC-GQ-002** | Unsupported; the response field is always null (R4, R6), so either the call fails or prior state is silently ignored. High / high (literal), high / medium (variable). | G3 |
| FC-BC-003 hosted tools | adapt → **FC-GQ-003** | Built-in tools exist but only `browser_search`/`code_interpreter` on GPT-OSS models, plus `mcp` on `/responses` (R9, T8). Flag a non-function tool type that Groq does not document (e.g. `web_search`, `file_search`, `computer_use_preview`, `image_generation`), or a built-in tool with a literal model outside GPT-OSS. Both endpoints. | G3 |
| FC-BC-004 `background=True` | **drop** | Not a documented Groq request field (R8); no Groq meaning. Revisit only if G5 shows a defined behaviour. | G6 |
| FC-BC-005 budget > 32 000 | adapt → **FC-GQ-005** | Limit is per model (§1.8: 65 536 GPT-OSS, 16 384 Qwen 3.8, …). Literal `max_completion_tokens`/`max_tokens`/`max_output_tokens` above the literal model's maximum; medium / high. | G3 |
| FC-BC-006 model catalogue | adapt → **FC-GQ-006** | Literal model not in the 18 Nov 2026 catalogue → medium/medium; a decommissioned ID (M2) → high/high "requests return errors"; preview model → info; Enterprise-only → info. | G3 |
| FC-BC-007 `store` omitted | **drop** | Groq's `store` defaults to `false` (R5); omitting it is correct. | G6 |
| FC-BC-008 input modality | **drop** (for now) | Groq claims text and image input only, but rejection of audio/file input is not documented (R13). G5 probes; a rule only if verified. | G6 (G5 decides) |
| FC-BC-009 (reserved) | retire with the family | Groq's request ID lives in the body (`x_groq.id`, E10); a neutral "request ID never captured" rule can take a new FC-GEN ID later (P1). | G6 |

### 4.3 Robustness and security rules

| Existing   | Decision | Change                                                                                                                                                                       | Phase |
| ---------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| FC-GEN-001 Chat truncation | keep | Cite Groq: `length` is a documented `finish_reason` (C11); handling guidance is OpenAI-convention (C12).                                                                       | G3 (text) |
| FC-GEN-002 Responses truncation | keep | `status: "incomplete"` + `incomplete_details` documented (R10).                                                                                                        | G3 (text) |
| FC-GEN-003 stream terminal events | keep, adapt | Chat: `data: [DONE]` (ST1); also treat `x_groq.error` as a failure (ST3). Responses: events unverified on Groq (R15).                                               | G3 |
| FC-GEN-004 no error handling | keep, adapt text | Remediation: retry 429 (honour `retry-after`), 500/502/503 with back-off; do not retry other 4xx; log `x_groq.id` (E1–E5, E9, E10).                                       | G3 |
| FC-GEN-005 no timeout | keep, adapt | Also recognise `groq.Groq`/`AsyncGroq`. Their default is 60 s (K5), not 10 min: lower severity to info for Groq clients, or skip them; G3 decides with a test.          | G2 (constructors) / G3 |
| FC-GEN-006 tool args parsing | keep | `arguments` is a JSON string (T4); Groq also returns 400 `failed_generation` on invalid calls (T6).                                                                            | G3 (text) |
| FC-GEN-007 tool output not a string | keep | Chat `content` must be a string (T5); Responses path stays OpenAI-convention (T10).                                                                                      | G3 (text) |
| FC-GEN-008/009 (reserved) | keep reserved | Neutral meanings unchanged.                                                                                                                                         | — |
| FC-SEC-001 hard-coded key | keep, adapt | Add the `gsk_` prefix (A4) and placeholders `gsk_your_secret_key_here`, `gsk_...`; remediation names `GROQ_API_KEY`. `bc_live_` detection stays until G6.                  | G3 |
| FC-SEC-002 credential logged | keep | Neutral.                                                                                                                                                                    | — |
| FC-SEC-003 (reserved) | keep reserved | Groq recommends `.env` (libs), which makes this P1 rule more relevant.                                                                                               | — |
| `core/redact.py` | adapt | Add the `gsk_` key pattern and `GROQ_API_KEY` as a secret env name before any Groq traffic exists.                                                                          | G2 |

### 4.4 New Groq rules (`FC-GQ-*`, G3 unless marked)

| ID        | Title                                                    | Detection (literal values only)                                                                                                         | Severity / confidence                    | Basis            |
| --------- | -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | ---------------- |
| FC-GQ-001 | `store=True` on `/responses`                             | as FC-BC-001                                                                                                                            | high / high                              | R4, R5           |
| FC-GQ-002 | `previous_response_id` used                              | as FC-BC-002                                                                                                                            | high / high; high / medium (variable)    | R4, R6           |
| FC-GQ-003 | Unsupported hosted/built-in tool                         | see §4.2                                                                                                                                | high / high (literal), medium (one hop)  | R9, T8           |
| FC-GQ-004 | Unsupported Responses parameter                          | `/responses` call with `include`, `safety_identifier`, `prompt_cache_key` or `prompt` (not `truncation`: R7 conflict)                   | medium / high                            | R4               |
| FC-GQ-005 | Output budget above the model's maximum                  | see §4.2                                                                                                                                | medium / high                            | §1.8             |
| FC-GQ-006 | Model not in Groq's catalogue / decommissioned / preview | see §4.2                                                                                                                                | high/high, medium/medium, info           | §1.8, M1, M2     |
| FC-GQ-007 | Chat parameter rejected by Groq                          | `logprobs`, `logit_bias` or `top_logprobs` given with a literal other than `None` (docs: 400 "if they are supplied"; whether `False` counts is [UNVERIFIED], so `False` is not flagged)                                              | high / high                              | C2               |
| FC-GQ-008 | `n` other than 1                                         | literal `n` ≠ 1                                                                                                                         | high / high                              | C4               |
| FC-GQ-009 | Parameter with no effect on Groq                         | `frequency_penalty`/`presence_penalty` non-zero, `metadata` on Chat                                                                     | low / medium                             | C5               |
| FC-GQ-010 | Deprecated parameter                                     | Chat `max_tokens` (use `max_completion_tokens`), `functions`/`function_call` (use `tools`/`tool_choice`)                                | low / high                               | C6, C9           |
| FC-GQ-011 | Structured output on an unsupported model or mode        | `json_schema` with a literal model outside S3; `strict: True` with a model outside the strict list; `response_format`/`text.format` together with `stream=True` or `tools` | medium / high                | S3, S5, T9       |
| FC-GQ-012 | Strict schema not strict-compatible                      | literal `strict: True` schema whose object lacks `additionalProperties: False` or leaves properties out of `required`                   | medium / medium                          | S2               |
| FC-GQ-013 | JSON object mode without JSON instructions               | `{"type": "json_object"}` with literal messages none of which mention "json"                                                            | low / medium (heuristic, ADR-013)        | S6               |
| FC-GQ-014 | Invalid reasoning parameters                             | `reasoning_effort` literal outside the literal model's set (e.g. `"none"` on GPT-OSS) → 400; `reasoning_format` on GPT-OSS; `reasoning_format` with `include_reasoning`; `reasoning_format="raw"` with JSON mode or tools → 400 | high / high (documented 400s); medium / high (others) | M4, M5, S8 |
| FC-GQ-015 | Wrong base URL for the client library                    | `groq.Groq(base_url=…/openai/v1)` (double prefix) or `openai.OpenAI(base_url="https://api.groq.com")` without `/openai/v1`               | high / high                              | A1, K4           |
| FC-GQ-016 | `name` on a message (held)                               | literal `"name"` key on a message dict → 400 per C2, contradicted by C3                                                                 | `hypothesis` until G5, then decide      | C2, C3 (G5)      |
| FC-GQ-017 | `responses` called on the `groq` SDK                     | `<groq client>.responses.create(...)` — the SDK has no such resource                                                                    | high / medium (SDK-version dependent)   | R14, K8          |
| FC-GQ-018 | Too many stop sequences / tools                          | literal `stop` list > 4; literal `tools` list > 128                                                                                     | medium / high                            | C7, T1           |
| FC-GQ-019 | `stream_options` without streaming                       | `stream_options` given and `stream` not literal `True`                                                                                  | low / high                               | C10              |
| FC-GQ-020 | `parallel_tool_calls=True` on a model without it (P1)    | literal `True` with a GPT-OSS model                                                                                                     | info / high                              | T7               |

Not proposed as rules: `temperature=0` (converted, harmless: C8); `service_tier` (valid
values only); flex without retries (runtime concern; FC-GEN-004 covers missing handling).

### 4.5 Runtime checks (`FC-RT-*`)

| Check      | Decision | Groq meaning / change                                                                                                                                  | Phase |
| ---------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ----- |
| FC-RT-001 store is false | adapt | Groq: `store` must not be `true`; omitting it is fine (R5). That is a different meaning from "store is sent as false", so it takes a new ID, **FC-RT-009** "store is not true"; FC-RT-001 retires in G6. | G4 |
| FC-RT-002 no previous_response_id | keep | Same meaning (R4).                                                                                                                             | G4 |
| FC-RT-003 only function tools | adapt | Built-in tools allowed for GPT-OSS models (R9). Meaning changes → new **FC-RT-015** "only tools the model supports"; FC-RT-003 retires in G6. | G4 |
| FC-RT-004 no background | drop | Undocumented on Groq (R8).                                                                                                                     | G6 |
| FC-RT-005 input modalities | drop (for now) | Unverified (R13); G5 decides.                                                                                                             | G6 |
| FC-RT-006 budget ≤ 32 000 | adapt | Per-model maximum (§1.8); same meaning, same ID.                                                                                                 | G4 |
| FC-RT-007 model in catalogue | adapt | Groq catalogue; same ID.                                                                                                                         | G4 |
| FC-RT-008 baseline exits 0 | keep | Neutral.                                                                                                                                            | — |
| FC-RT-011…014 tool round | keep | Chat round trip verified by Groq (T4, T5); Responses round trip OpenAI-convention (T10). Update remediation text.                                    | G4 |
| FC-RT-020 truncation surfaced | keep | `length` / `incomplete` documented (C11, R10).                                                                                                   | G4 (text) |
| FC-RT-022 incomplete tool call not executed | keep | FlightCheck recommendation (not a Groq statement); note that Groq may instead return 400 `failed_generation` (T6).                          | G4 (text) |
| FC-RT-023 no identical retry after truncation | keep | FlightCheck recommendation (BharatCode's documented advice has no Groq counterpart); wording must not cite Groq.                          | G4 (text) |
| FC-RT-030 stream failure surfaced | keep, adapt | Mock adds a Chat chunk with `x_groq.error` then ends (ST3).                                                                                 | G4 |
| FC-RT-040 503 retried | keep, adapt | Groq: 503 "wait before retrying" (E3). Remediation drops "model busy".                                                                          | G4 |
| FC-RT-041 back-off present | keep | Groq recommends exponential back-off (E9). `x-stainless-retry-count` is sent by both SDKs (K6).                                                  | G4 |
| FC-RT-050 only the fake key | keep, adapt | Harness injects the fake key into `GROQ_API_KEY` and points `GROQ_BASE_URL` at the mock **root** and `OPENAI_BASE_URL` at `<mock>/openai/v1` (K3, K4). | G4 |
| FC-RT-060 request limit | keep | Neutral.                                                                                                                                            | — |
| FC-RT-010/031/042/043 (P1 reserved) | keep reserved | 042 (retries bounded) and 043 (4xx not retried) apply to Groq unchanged; 031 (keepalive) has no documented Groq basis.                    | — |
| **FC-RT-044** (new) | add | "429 retry waits for `retry-after`": after a 429 with `retry-after: N`, the next request comes ≥ N s later (E5, K6). Scenario S11.             | G4 |
| **FC-RT-045** (new, P1) | add | "Invalid tool call 400 handled": after a 400 with `failed_generation`, the app retries or exits non-zero without crashing (T6).              | G4 (P1) |

### 4.6 Mock faults (`mock/faults.py`)

| Action               | Decision | Groq change                                                                                                                                         | Phase |
| -------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| `OK_TEXT`            | keep     | Chat bodies add `x_groq.id`, `usage.queue_time`/`prompt_time`/`completion_time`/`total_time`, `system_fingerprint`, `service_tier` (C13).            | G4 |
| `TOOL_CALL`          | keep     | `finish_reason: "tool_calls"`, string `arguments` (T4).                                                                                             | G4 |
| `TRUNCATE`           | keep     | `finish_reason: "length"` / `status: "incomplete"` (C11, R10).                                                                                      | G4 |
| `TRUNCATE_TOOL_CALL` | keep     | Unverified whether Groq ever returns a cut-off tool call or a 400 `failed_generation` instead (T6); keep the action, label it `[UNVERIFIED]` for Groq. | G4 |
| `HTTP_ERROR`         | adapt    | Groq error body `{"error": {"message", "type"}}` (E1); add a `headers` param for `retry-after` and `x-ratelimit-*` (E5, E6); allow 413, 422, 498.   | G4 |
| `STREAM_FAIL_AFTER`  | adapt    | Chat: N deltas, then a chunk with `x_groq.error`, then end (wire format [UNVERIFIED], ST3). Responses: `response.failed` (OpenAI shape, [UNVERIFIED] for Groq). | G4 |
| `KEEPALIVE_STREAM`   | keep as generic | No Groq basis (ST4); used only by P1 S09; never cited as Groq behaviour.                                                                    | G4 (text) |
| `DELAY`              | keep     | Neutral.                                                                                                                                            | — |
| new `TOOL_VALIDATION_ERROR` | add (P1) | 400 with `failed_generation` (T6), for FC-RT-045.                                                                                            | G4 |
| Routes               | adapt    | Serve `/openai/v1/chat/completions`, `/openai/v1/responses`, `/openai/v1/models`, so both the `groq` SDK (root base URL) and the OpenAI SDK (`/openai/v1` base) reach the mock (K4). Documented Groq 400s (C2, C4, M4, S8) enforced through `core/contract.py`. | G4 |

### 4.7 Scenarios

| Scenario | Decision | Groq change                                                                                                                         | Phase |
| -------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------- | ----- |
| S01 baseline | keep | —                                                                                                                                 | G4 |
| S02 busy 503 ×2 | keep, adapt | Still meaningful (E3); body in Groq's error format.                                                                         | G4 |
| S03 truncation | keep | —                                                                                                                                   | G4 |
| S04 tool round | keep | —                                                                                                                                   | G4 |
| S05 truncated tool call | keep | FlightCheck recommendation, see `TRUNCATE_TOOL_CALL`.                                                                  | G4 |
| S06 stream failure | keep, adapt | Groq `x_groq.error` chunk variant for Chat streams.                                                                     | G4 |
| **S11** rate limited (new) | add | 429 with `retry-after: 2` and `x-ratelimit-*` headers, then OK; FC-RT-044.                                         | G4 |
| S02b/S07–S10 (P1) | keep reserved | S02b, S07 apply to Groq unchanged; S08 (history replay) matches R3; S09 (keepalive) has no Groq basis.            | — |

### 4.8 Other components

| Component                                    | Change                                                                                                                                               | Phase |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| `core/config.py` constants                   | Add `GROQ_BASE_URL`, `GROQ_API_KEY_ENV`, `GROQ_DEFAULT_MODEL`, the dated model catalogue (per-model max completion tokens, strict-schema, built-in-tool, parallel and reasoning sets) and decommissioned IDs. BharatCode constants stay until G6. | G2 |
| `scanner/detect.py`                          | New provider `groq`: host `api.groq.com` (high), `GROQ` in env-var names (medium), `groq.Groq`/`AsyncGroq` constructors (high), documented Groq model IDs (supporting, ADR-024 item 2). Tie-break order decided in a new ADR. Note: Groq model IDs such as `openai/gpt-oss-20b` are also served by other providers, so they stay supporting only. | G2 |
| `providers/capabilities.py`                  | `GROQ_PROFILE` from §1; several capabilities are model-dependent (built-in tools, strict schema, `reasoning_effort: none`, parallel tools): G2 decides how the provider-level profile expresses that (likely `supported_documented` with a "model-dependent" note). | G2 |
| `providers/` adapter                          | `GroqAdapter` (raw HTTP, §3 settings), pre-flight from §1, `x_groq.id` capture, retry policy honouring `retry-after`.                                 | G5 |
| `diagnostics/` (`explain`)                   | `--llm groq`, consent panel naming Groq, contract excerpts from this file, strict JSON-schema output.                                                 | G5 |
| Live verification                            | Replace [UNVERIFIED] rows with dated results (live tests marked `live`; key from env only).                                                         | G5 |
| Fixture apps, `expected_findings.json`, `expected_runtime.json` | Add Groq fixtures in G3/G4; retire `bc_*` fixtures in G6.                                                                     | G3, G4, G6 |
| Docs (README, RULES, ARCHITECTURE, BHARATCODE_CONTRACT) | RULES.md gains FC-GQ rules in G3; BharatCode docs removed or archived in G6.                                                           | G3, G6 |

### 4.9 Existing issues found during research (not fixed in G1)

- FC-GEN-005's text says the SDK default timeout is 10 minutes; true for `openai`, false for
  `groq` (60 s, K5). Harmless today (the rule does not see `groq` clients). → G3.
- The runtime harness sets only BharatCode/OpenAI variables; a `groq`-SDK app under `test`
  would read `GROQ_BASE_URL`/`GROQ_API_KEY` and could reach the real host if those are set in
  the developer's shell (the egress guard is best effort, ADR-017). → G4 must scrub and set them.

---

## Sources

All read on **18 November 2026** (Asia/Kolkata). Docs pages were fetched as their Markdown
rendering (`<URL>.md`).

| Key     | Source                                                                       | Used for                                       |
| ------- | ---------------------------------------------------------------------------- | ---------------------------------------------- |
| openai  | <https://console.groq.com/docs/openai>                                       | A1, A8, C2, C4, C8                             |
| api     | <https://console.groq.com/docs/api-reference>                                | A5, C1, C4–C10, C13, C14, R5–R10, T1, T2, M4   |
| resp    | <https://console.groq.com/docs/responses-api>                                | R1–R4, R9, R11–R13                             |
| chat    | <https://console.groq.com/docs/text-chat>                                    | C7, streaming and async usage                  |
| so      | <https://console.groq.com/docs/structured-outputs>                           | S1–S7, T9                                      |
| tools   | <https://console.groq.com/docs/tool-use/overview>                            | T4, T5, T7, T8                                 |
| local   | <https://console.groq.com/docs/tool-use/local-tool-calling>                  | C3, T1–T6                                      |
| builtin | <https://console.groq.com/docs/tool-use/built-in-tools>                      | T8                                             |
| reason  | <https://console.groq.com/docs/reasoning>                                    | S8, M3, M5, X9                                 |
| errors  | <https://console.groq.com/docs/errors>                                       | E1–E3                                          |
| limits  | <https://console.groq.com/docs/rate-limits>                                  | E4–E7                                          |
| models  | <https://console.groq.com/docs/models>                                       | A6, §1.8, M1, M6                               |
| depr    | <https://console.groq.com/docs/deprecations>                                 | §1.8, M1, M2                                   |
| libs    | <https://console.groq.com/docs/libraries>                                    | A3, A8, K1                                     |
| quick   | <https://console.groq.com/docs/quickstart>                                   | A3                                             |
| sec     | <https://console.groq.com/docs/production-readiness/security-onboarding>     | A4, A9, E9, X1                                 |
| check   | <https://console.groq.com/docs/production-readiness/production-ready-checklist> | ST5, X10                                    |
| tiers   | <https://console.groq.com/docs/service-tiers>                                | C14                                            |
| flex    | <https://console.groq.com/docs/flex-processing>                              | E2 (498 `capacity_exceeded`)                   |
| perms   | <https://console.groq.com/docs/model-permissions>                            | A7                                             |
| sdk     | <https://github.com/groq/groq-python> at tag `v1.7.0`: `src/groq/_client.py`, `_constants.py`, `_base_client.py`, `_exceptions.py`, `resources/`, `types/chat/chat_completion.py`, `types/chat/chat_completion_chunk.py` | C11, C13, ST2, ST3, R14, K2–K8 |
| pypi    | <https://pypi.org/pypi/groq/json>                                            | K1                                             |

Also read, not used for facts: <https://console.groq.com/llms.txt> (the docs index),
<https://console.groq.com/docs/prompt-caching> (cached tokens: E4 cites the rate-limits page),
<https://console.groq.com/docs/spend-limits>.
