## ADDED Requirements

### Requirement: Environment-driven configuration
The system SHALL determine the active LLM backend solely from environment variables. Given the same environment, resolution SHALL be a pure function that produces the same resolved `(provider, base_url, model, api_key)` tuple — no runtime discovery, heuristic fallback, or implicit default outside the rules in this spec.

### Requirement: APP_ENV validation
The system SHALL support exactly the `APP_ENV` values `development`, `preprod`, and `production`. If `APP_ENV` is unset, empty, or not one of these values, the system SHALL fail fast at startup and SHALL NOT fall back to any provider — in particular it SHALL NOT silently resolve to the production-like OpenAI backend.

#### Scenario: APP_ENV missing fails fast
- **WHEN** the process starts with `APP_ENV` unset or empty
- **THEN** it exits with code `78` and an `ERROR` log naming `APP_ENV` as missing
- **THEN** it does NOT resolve to the OpenAI / `gpt-5.6-luna` backend

#### Scenario: APP_ENV unknown fails fast
- **WHEN** the process starts with `APP_ENV=prod` (or any value outside `development`, `preprod`, `production`)
- **THEN** it exits with code `78` and an `ERROR` log naming `APP_ENV` and the allowed values
- **THEN** it does NOT silently resolve to a production-like backend

### Requirement: Pinned default backends
With no explicit overrides present, the resolved tuple SHALL be exactly the row of the resolved `APP_ENV`:

| `APP_ENV` | `provider` | `base_url` | `model` | `api_key` |
|---|---|---|---|---|
| `development` | `openai_compatible` | `http://localhost:11434/v1` | `gemma-4-e2b-it` | `local-dev` (sentinel, never read from `OPENAI_API_KEY`) |
| `preprod` | `openai` | `https://api.openai.com/v1` | `gpt-5.6-luna` | `OPENAI_API_KEY` (required) |
| `production` | `openai` | `https://api.openai.com/v1` | `gpt-5.6-luna` | `OPENAI_API_KEY` (required) |

The development `api_key` SHALL be the fixed sentinel string `local-dev` so a real key can never reach a local (insecure) transport. Any other local endpoint or model tag (LM Studio, vLLM, a different Gemma variant) SHALL be selected only by explicit override — this spec defines exactly one development default and no additional configuration variable.

#### Scenario: Pinned development defaults
- **WHEN** the configuration is resolved with `APP_ENV=development` and no `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, or `OPENAI_API_KEY` set
- **THEN** the resolved tuple is exactly `(provider=openai_compatible, base_url=http://localhost:11434/v1, model=gemma-4-e2b-it, api_key=local-dev)`
- **THEN** no `OPENAI_API_KEY` is required

#### Scenario: Pinned production defaults
- **WHEN** the configuration is resolved with `APP_ENV=production`, a valid non-empty `OPENAI_API_KEY`, and no overrides
- **THEN** the resolved tuple is exactly `(provider=openai, base_url=https://api.openai.com/v1, model=gpt-5.6-luna, api_key=<OPENAI_API_KEY>)`

#### Scenario: Pinned preprod defaults
- **WHEN** the configuration is resolved with `APP_ENV=preprod`, a valid non-empty `OPENAI_API_KEY`, and no overrides
- **THEN** the resolved tuple is exactly `(provider=openai, base_url=https://api.openai.com/v1, model=gpt-5.6-luna, api_key=<OPENAI_API_KEY>)`, identical to production

### Requirement: Partial-override composition rule
Overrides SHALL be composed per variable: each of `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_BASE_URL`, when set to a non-empty value, SHALL replace exactly the corresponding component of the resolved tuple; every component with no override SHALL keep the `APP_ENV` default component. The system SHALL accept mixed states (e.g. `APP_ENV=production` with only `LLM_MODEL` set) and SHALL NOT validate cross-field consistency. `OPENAI_API_KEY` is a secret, not a provider component: it has no position in the composition rule and SHALL be used as `api_key` only when the resolved `provider` is `openai`.

#### Scenario: Partial override composes per variable
- **WHEN** the configuration is resolved with `APP_ENV=production`, a valid `OPENAI_API_KEY`, and only `LLM_MODEL=other-model` set
- **THEN** the resolved tuple is `(provider=openai, base_url=https://api.openai.com/v1, model=other-model, api_key=<OPENAI_API_KEY>)` — only `model` differs from the `APP_ENV` default
- **THEN** no error is raised for the mixed state

### Requirement: LLM_PROVIDER validation
`LLM_PROVIDER` SHALL accept exactly the values `openai` and `openai_compatible`. If `LLM_PROVIDER` is set to any other value, the system SHALL fail fast at startup.

#### Scenario: Unknown LLM_PROVIDER fails fast
- **WHEN** the process starts with `LLM_PROVIDER=anthropic` (or any value outside `openai`, `openai_compatible`)
- **THEN** it exits with code `78` and an `ERROR` log naming `LLM_PROVIDER` and the allowed values

### Requirement: Startup fail-fast contract
Every startup configuration error defined in this spec (invalid/missing `APP_ENV`, invalid `LLM_PROVIDER`, missing/empty key for the resolved `openai` provider, invalid `LLM_TIMEOUT_SECONDS` (must be a positive integer), or invalid `LLM_MAX_RETRIES` (must be a non-negative integer)) SHALL abort startup: exit code `78` (EX_CONFIG) without binding the service port, and an `ERROR` log line naming the offending variable and the allowed values. "Missing or empty" for `OPENAI_API_KEY` SHALL mean the variable is unset, empty, or whitespace-only.

#### Scenario: Missing key with resolved openai provider
- **WHEN** the process starts with `APP_ENV=production` and `OPENAI_API_KEY` unset
- **THEN** it exits with code `78`, does not bind the service port, and the `ERROR` log names `OPENAI_API_KEY`

#### Scenario: Empty key is missing
- **WHEN** the resolved provider is `openai` and `OPENAI_API_KEY` is set to `""` or to whitespace only (e.g. `"   "`)
- **THEN** the process exits with code `78` exactly as when the variable is unset

### Requirement: Provider-driven key check
The `OPENAI_API_KEY` requirement SHALL apply to the resolved provider, not to the environment: the system SHALL refuse to start whenever the resolved provider is `openai` and `OPENAI_API_KEY` is missing or empty, regardless of `APP_ENV`. The system SHALL NOT require `OPENAI_API_KEY` when the resolved provider is `openai_compatible`, even under `APP_ENV=preprod` or `production`.

#### Scenario: Local override in production does not require the cloud key
- **WHEN** the process starts with `APP_ENV=production`, `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=gemma-4-e2b-it`, and no `OPENAI_API_KEY`
- **THEN** it starts successfully, because the key check applies to the resolved provider `openai_compatible`, which needs no key

### Requirement: Startup logging
The system SHALL log, at `INFO` level, the resolved `APP_ENV`, provider, model, and base URL at startup and at any time the configuration changes. This log line is the verify-after-change signal for deploys and SHALL appear in normal logs without a debug flag.

#### Scenario: Startup log contains the resolved configuration
- **WHEN** the process starts with any valid configuration
- **THEN** an `INFO` log line contains the resolved `APP_ENV`, provider, model, and base URL

### Requirement: Single client abstraction
The application SHALL interact with the LLM only through a single client abstraction (`LLMClient`), which SHALL expose text-only completion and vision (image + text) completion, each with optional structured output. Handlers SHALL receive the client by construction (dependency injection) and SHALL NOT instantiate provider clients themselves.

#### Scenario: Successful text completion via stubbed transport
- **WHEN** `complete()` is called with text-only messages against a client constructed with a stub transport that returns a well-formed 200 chat-completions body
- **THEN** an `LLMResponse` is returned with the content populated
- **THEN** no provider-specific type or error escapes the client to the caller

### Requirement: Stub-transport test seam
The client SHALL be constructable with an injected transport (a minimal request/response seam). Tests of the client contract — including every error-mapping and retry path — SHALL be runnable against a stub transport, without any real provider, network access, or local model server. The clock used for timeouts and backoff SHALL likewise be injectable so boundary tests are deterministic.

### Requirement: Domain error set
Provider-specific types and error details SHALL NOT leak into the rest of the application: every failure SHALL surface as exactly one of the following five domain errors, each carrying a human-readable `detail` message (e.g. HTTP status, truncated raw body for parse failures) so the mapping is diagnosable.

| Domain error | Raised when |
|---|---|
| `timeout` | The client-side deadline (`LLM_TIMEOUT_SECONDS`) expired before a response |
| `rate_limit` | The provider rejected the request as rate-limited |
| `invalid_response` | The provider accepted the request but the body cannot be used (HTTP error, malformed structure, or malformed/schema-mismatched structured JSON) |
| `provider_unavailable` | The provider could not be reached, or is overloaded (5xx) |
| `vision_not_supported` | A vision request was issued but the resolved local model/endpoint does not support images |

`vision_not_supported` is a first-class member of the domain set (the earlier four-class list was incomplete): it is the only way a caller can distinguish a permanent environment-level condition from a transient outage.

### Requirement: Failure-to-domain-error mapping
Every client failure SHALL map to exactly one domain error, as follows:

| Failure condition | Domain error |
|---|---|
| Client-side timeout (deadline `LLM_TIMEOUT_SECONDS` exceeded) | `timeout` |
| Connection refused / DNS failure / network unreachable | `provider_unavailable` |
| HTTP 429 (with or without `Retry-After`) | `rate_limit` |
| HTTP 401 or 403 | `invalid_response` |
| HTTP 400 or 404 (including "model not found") | `invalid_response` |
| HTTP 5xx | `provider_unavailable` |
| HTTP 200 with a structurally invalid chat-completions body (missing `choices`) | `invalid_response` |
| HTTP 200 with structured output requested but `choices[].message.content` is not valid JSON | `invalid_response` |
| HTTP 200 with structured output requested but the parsed JSON does not match the declared schema (required field missing or wrong type) | `invalid_response` |
| Vision request to a local endpoint whose model has no vision capability — signalled by a provider error response (400/404/422) or the endpoint's model metadata | `vision_not_supported` |
| Vision request to a local endpoint with no other distinguishing signal (e.g. server unreachable, or an error that could also be a transient outage) | `provider_unavailable` (conservative default; the caller must treat it as transient) |
| Unknown error class (nothing above) | `provider_unavailable` (conservative default) |

The caller of a failed vision request SHALL be able to distinguish `vision_not_supported` from every other domain error, in particular from `provider_unavailable`.

#### Scenario: Failure mapping is exact
- **WHEN** a request is made with `LLM_MAX_RETRIES=0` against a stub transport returning the failure below
- **THEN** the raised domain error is exactly the mapped one, for each row:
  - HTTP 429 → `rate_limit`
  - client-side timeout (deadline exceeded before any response) → `timeout`
  - connection refused / DNS failure → `provider_unavailable`
  - HTTP 401 or 403 → `invalid_response`
  - HTTP 500 → `provider_unavailable`
  - HTTP 200 whose body has no `choices` → `invalid_response`
  - HTTP 200, structured output requested, `choices[0].message.content` = `{not json` → `invalid_response`
  - HTTP 200, structured output requested, content `{"unrelated": true}` against a schema requiring field `candidates` → `invalid_response`
  - no provider-specific type or raw exception escapes the client in any case

### Requirement: Timeout and retry policy
The client SHALL enforce a per-request client-side timeout `LLM_TIMEOUT_SECONDS` (default `30`) on every attempt, and SHALL retry only the retryable set:
- **Retryable:** `timeout`, `rate_limit`, and `provider_unavailable` when caused by a 5xx response.
- **Never retried:** `invalid_response`, `vision_not_supported`, and `provider_unavailable` caused by connection-level failures (refused / DNS / unreachable).

`LLM_MAX_RETRIES` SHALL default to `2` (up to 3 total attempts: the initial call plus 2 retries). `LLM_MAX_RETRIES=0` SHALL mean no retries; a negative value SHALL be a configuration error subject to the startup fail-fast contract. The client SHALL back off exponentially with full jitter — delay = `random() in [0, min(cap, base * 2^attempt)]`, `base = 1s`, `cap = 10s`, `attempt` = zero-based retry index — and SHALL honour a `Retry-After` header on `rate_limit` responses when present (its value replaces the computed backoff for that retry). On exhaustion the client SHALL raise the last domain error unchanged.

#### Scenario: Retryable errors are retried, then succeed
- **WHEN** a request is made with `LLM_MAX_RETRIES=2` and an injected clock, against a stub transport that fails twice with HTTP 429 and then returns a well-formed 200
- **THEN** it succeeds after exactly 3 total attempts
- **THEN** the injected clock shows exactly 2 backoff waits, each within `[0, min(10, 1 * 2^attempt)]` seconds for the zero-based retry index
- **THEN** if a 429 carried a `Retry-After` header, that value was used instead of the computed backoff

#### Scenario: Retryable errors are exhausted
- **WHEN** a request is made with `LLM_MAX_RETRIES=2` against a stub transport that always fails with HTTP 500
- **THEN** it is attempted exactly 3 times and then raises `provider_unavailable` (the last error)

#### Scenario: Non-retryable errors are not retried
- **WHEN** a request is made with `LLM_MAX_RETRIES=2` against a stub transport that always fails with HTTP 401
- **THEN** it is attempted exactly once and raises `invalid_response`

#### Scenario: LLM_MAX_RETRIES=0 means no retries
- **WHEN** a request is made with `LLM_MAX_RETRIES=0` against a stub transport that always fails with HTTP 500
- **THEN** it is attempted exactly once and raises `provider_unavailable`

#### Scenario: Negative LLM_MAX_RETRIES fails fast
- **WHEN** the process starts with `LLM_MAX_RETRIES=-1`
- **THEN** it exits with code `78` and an `ERROR` log naming `LLM_MAX_RETRIES`

### Requirement: Local vision failure outcome
When a vision request targets a local model without vision support, the client SHALL raise `vision_not_supported` when the endpoint signals it (provider error response 400/404/422, or model metadata indicating no vision), and SHALL raise `provider_unavailable` when no distinguishing signal exists (e.g. the server is unreachable or the error could also be a transient outage). The add flow SHALL treat `vision_not_supported` as a permanent, environment-level condition (clear user-facing message, e.g. "image add is not available in this environment") and SHALL treat `provider_unavailable` from the same call as transient.

#### Scenario: Local vision failure is vision_not_supported
- **WHEN** `complete_with_vision()` is called in `development` against a local endpoint that rejects image input with a 400 ("model does not support vision")
- **THEN** `vision_not_supported` is raised
- **THEN** a caller that catches only `vision_not_supported` handles the failure without catching any other domain error

#### Scenario: Local vision failure with no distinguishing signal is provider_unavailable
- **WHEN** `complete_with_vision()` is called in `development` against a local endpoint that is unreachable (connection refused)
- **THEN** `provider_unavailable` is raised, NOT `vision_not_supported`

### Requirement: Structured output failure
When structured output is requested, a provider response whose `choices[].message.content` is not valid JSON, or whose parsed JSON does not match the declared schema (required field missing or wrong type), SHALL raise `invalid_response` without retry, and the raised error's `detail` SHALL preserve the raw (truncated) body for diagnosis. A well-formed response SHALL be parsed into structured data on `LLMResponse`.

#### Scenario: Structured-output malformed JSON
- **WHEN** `complete()` is called with `response_format` requesting structured JSON and the stub transport returns HTTP 200 with `choices[0].message.content` that is not valid JSON
- **THEN** `invalid_response` is raised and no retry is performed
- **THEN** the raised error's `detail` preserves the raw (truncated) body

#### Scenario: Structured-output well-formed JSON
- **WHEN** `complete()` is called with `response_format` and the stub transport returns HTTP 200 with `content` equal to a JSON object matching the declared schema
- **THEN** `LLMResponse` is returned and its parsed structured data matches the schema

### Requirement: Behaviour across environments
The same application code path SHALL be used for extraction, back generation, and short replies regardless of which backend is active; only the resolved configuration may differ.

#### Scenario: Identical code path across resolved backends
- **WHEN** the same handler code performs extraction / back generation / a short reply, once with a development-resolved client and once with a production-resolved client
- **THEN** the same code path executes in both cases and only the resolved configuration differs

#### Scenario: Vision in production uses the same contract
- **WHEN** `complete_with_vision()` is called in `production` (valid key) against a stubbed OpenAI endpoint that accepts image input
- **THEN** the request is sent to `https://api.openai.com/v1` and the response is parsed through the same `LLMResponse` contract as text completion
