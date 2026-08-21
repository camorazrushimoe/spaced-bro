# Design: LLM Router & Environment Configuration

## Goals

Provide a single, environment-aware LLM access point that the rest of SpacedBro uses for:
- Intent extraction (text)
- Vision extraction (images)
- Short `back` generation
- Characterful short replies

The implementation must be swappable by configuration only, and its configuration
resolution must be fully deterministic and unit-testable.

## Environment model

Primary signal: `APP_ENV`

| Value          | Meaning                          | Default LLM behaviour                          |
|----------------|----------------------------------|------------------------------------------------|
| `development`  | Local / CI / developer machine   | Local OpenAI-compatible server (Gemma 4)       |
| `preprod`      | Staging / pre-production         | OpenAI `gpt-5.6-luna`                          |
| `production`   | Live traffic                     | OpenAI `gpt-5.6-luna`                          |

`APP_ENV` supports **exactly** these three values. Unset, empty, or any other
value is a configuration error: the process fails fast (see fail-fast contract)
and **never** falls into the `preprod`/`production` branch implicitly. The old
"else: preprod or production" catch-all is removed on purpose — a typo
(`APP_ENV=prod`) must be a loud startup error, not a silent production-like
backend.

## Configuration (environment variables)

All configuration is read from the environment. No secrets in code or config
files committed to the repo.

```
APP_ENV=development|preprod|production   # required; exactly one of the three

# Explicit overrides (optional — composed per variable, see below)
LLM_PROVIDER=openai|openai_compatible    # required when set; exactly these two values
LLM_MODEL=<model name>
LLM_BASE_URL=<OpenAI-compatible base URL>
OPENAI_API_KEY=*** only consulted when resolved provider = openai>
LLM_TIMEOUT_SECONDS=30                   # optional, default 30
LLM_MAX_RETRIES=2                        # optional, default 2; 0 = no retries; < 0 = config error
```

There are exactly these variables plus `APP_ENV`. The development endpoint and
model are pinned (below) — a developer using a different local server or tag
selects it by overriding `LLM_BASE_URL` / `LLM_MODEL` / `LLM_PROVIDER`; no extra
config variable exists.

### Pinned default backends

| `APP_ENV` | `provider` | `base_url` | `model` | `api_key` |
|---|---|---|---|---|
| `development` | `openai_compatible` | `http://localhost:11434/v1` | `gemma-4-e2b-it` | `local-dev` (sentinel) |
| `preprod` | `openai` | `https://api.openai.com/v1` | `gpt-5.6-luna` | `OPENAI_API_KEY` (required) |
| `production` | `openai` | `https://api.openai.com/v1` | `gpt-5.6-luna` | `OPENAI_API_KEY` (required) |

With no overrides, the resolved tuple is exactly the row above, so tests can
assert all four fields in every environment. The development `api_key` is the
fixed sentinel string `local-dev` — it is never read from `OPENAI_API_KEY`, so a
real key can never leak into the local (insecure) transport.

### Default resolution logic (deterministic)

```
1. Validate APP_ENV in {development, preprod, production}; else ConfigError("APP_ENV")
2. Look up the pinned default row for APP_ENV
3. Per-variable composition:
     provider = LLM_PROVIDER if set (and valid) else default.provider
     base_url = LLM_BASE_URL if set else default.base_url
     model    = LLM_MODEL if set else default.model
   Validate LLM_PROVIDER (if set) in {openai, openai_compatible}; else ConfigError("LLM_PROVIDER")
4. api_key = OPENAI_API_KEY if provider == openai else "local-dev"
5. If provider == openai and api_key is missing/empty/whitespace → ConfigError("OPENAI_API_KEY")
6. Validate LLM_TIMEOUT_SECONDS (positive int, default 30) and
   LLM_MAX_RETRIES (non-negative int, default 2); violations → ConfigError
7. Log at INFO: resolved APP_ENV, provider, model, base_url
```

Notes:

- **Partial overrides compose per variable** — each set variable replaces only
  its own component; unset components keep the `APP_ENV` default. Mixed states
  (e.g. only `LLM_MODEL` set under `APP_ENV=production`) are legal and produce
  the mixed tuple; no cross-field consistency is validated.
- **The key check is provider-driven, not environment-driven:** it applies when
  the *resolved* provider is `openai`, whatever `APP_ENV` is. A fully-local
  override under `APP_ENV=preprod`/`production` (`LLM_PROVIDER=openai_compatible`
  + local URL/model) starts without `OPENAI_API_KEY`.
- **"Empty" means unset, empty string, or whitespace-only.**

### Fail-fast contract

Every configuration error (invalid/missing `APP_ENV`, invalid `LLM_PROVIDER`,
missing/empty key for the resolved `openai` provider, invalid
`LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES`) aborts startup:

- process exit code **78** (`EX_CONFIG`), before the service port is bound;
- `ERROR` log naming the offending variable and the allowed values
  (e.g. `APP_ENV must be one of: development, preprod, production`).

## Client interface (contract)

A thin abstraction (`LLMClient`) that the application code depends on. Handlers
receive the client by construction (dependency injection) — never by instantiating
it themselves.

Required capabilities for MVP:

1. **Text completion / chat** with structured output support (JSON schema or equivalent)
2. **Vision** — accept image bytes / URL + text prompt, return text (and structured data)
3. **Error handling** — every failure maps to exactly one of the five domain errors (table below)
4. **Timeout & retry** — configurable, pinned defaults (below)

The interface MUST NOT expose provider-specific types to the rest of the codebase.

Suggested minimal surface (illustrative):

```python
class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[Message],
        *,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    async def complete_with_vision(
        self,
        messages: list[Message],  # may contain image parts
        *,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...
```

Implementation details (OpenAI SDK vs raw HTTP, local adapter) live behind this
interface.

### Test seam (normative)

`LLMClient` MUST be constructable with an injected **transport** — a minimal
request/response seam (e.g. an object implementing `async post(path, *, headers,
json, timeout) -> (status, headers, body)`). All client contract tests, including
every error-mapping and retry case, MUST run against a stub transport with no
real provider, no network, and no local model server. The clock used for
timeouts/backoff MUST likewise be injectable so boundary tests are deterministic.

### Domain error set (exhaustive — five classes)

| Domain error | Raised when |
|---|---|
| `timeout` | Client-side deadline (`LLM_TIMEOUT_SECONDS`) expired before a response |
| `rate_limit` | Provider rejected the request as rate-limited |
| `invalid_response` | Provider accepted the request but the body cannot be used (auth/other 4xx, malformed structure, malformed or schema-mismatched structured JSON) |
| `provider_unavailable` | Provider could not be reached (transport-level) or is overloaded (5xx) |
| `vision_not_supported` | Vision request to a local endpoint whose model lacks image input |

`vision_not_supported` was added to the set (the original four-class list was
incomplete): it is the only way a caller can distinguish a permanent
environment-level condition from a transient outage and message the user
correctly.

### Failure-to-domain-error mapping

| Failure condition | Domain error |
|---|---|
| Client-side timeout | `timeout` |
| Connection refused / DNS failure / network unreachable | `provider_unavailable` |
| HTTP 429 | `rate_limit` |
| HTTP 401 / 403 | `invalid_response` |
| HTTP 400 / 404 (incl. "model not found") | `invalid_response` |
| HTTP 5xx | `provider_unavailable` |
| HTTP 200, body missing `choices` | `invalid_response` |
| HTTP 200, structured output requested, `content` not valid JSON | `invalid_response` |
| HTTP 200, structured output requested, JSON fails declared schema (required field missing / wrong type) | `invalid_response` |
| Vision to local endpoint: provider error response (400/404/422) or model metadata indicating no vision | `vision_not_supported` |
| Vision to local endpoint, no distinguishing signal (unreachable / ambiguous error) | `provider_unavailable` (conservative; treat as transient) |
| Anything else | `provider_unavailable` (conservative) |

Every raised domain error carries a human-readable `detail` (status code,
truncated raw body for parse failures) so the mapping is diagnosable without
provider types leaking.

### Timeout & retry policy (pinned)

- Per-request timeout: `LLM_TIMEOUT_SECONDS`, default **30**, client-side
  deadline on every attempt.
- `LLM_MAX_RETRIES` default **2** (≤3 total attempts). `0` disables retries;
  negative → fail-fast config error.
- **Retryable set:** `timeout`, `rate_limit`, `provider_unavailable` **caused by
  5xx**. **Never retried:** `invalid_response`, `vision_not_supported`,
  connection-level `provider_unavailable`.
- **Backoff:** exponential with full jitter —
  `delay = random() in [0, min(cap, base * 2^attempt)]`, `base = 1s`, `cap = 10s`,
  `attempt` = zero-based retry index. A `Retry-After` header on a `rate_limit`
  response replaces the computed delay for that retry.
- On exhaustion, raise the last domain error unchanged.
- Clock is injectable; tests assert attempt counts and that backoff waits stay
  within the pinned bounds.

## Local development expectations

- Developers run a local OpenAI-compatible server (Ollama, LM Studio, vLLM, etc.)
  with a Gemma 4 variant (E2B or E4B recommended), e.g. via Ollama on
  `localhost:11434`.
- The default points at `http://localhost:11434/v1` with model
  `gemma-4-e2b-it`. A different local server or tag is selected by overriding
  `LLM_BASE_URL` / `LLM_MODEL` — that is the intended mechanism, and the spec
  pins exactly one default so tests can assert it.
- Vision support in the local model is desirable but not a hard blocker for
  pure-text path testing. When vision is unavailable locally, the client MUST
  raise `vision_not_supported` (see mapping table) — a named domain error the
  add flow catches to tell the user "image add is not available in this
  environment". It is not a crash, not a `provider_unavailable`, and not a
  retryable condition.

## Production / preprod expectations

- Default model: `gpt-5.6-luna` (vision-capable, currently the cost-efficient OpenAI option suitable for high-volume short tasks).
- `OPENAI_API_KEY` is mandatory — enforced via the resolved provider, per the
  fail-fast contract.
- Soft per-user vision rate limit (already defined in mvp-core) continues to apply at the application layer.

## Observability

- At startup (and on any configuration change) log at **INFO**: resolved
  `APP_ENV`, provider, model, base URL. This is the verify-after-change signal
  for deploys — it must appear in normal logs, not only under a debug flag.
- Log token usage / latency per call at DEBUG level (or metrics).
- Full prompt/response logging is OFF by default and enabled only by the flag
  `LLM_LOG_PROMPTS=1` (never enabled implicitly in production).

## Non-goals for this change

- Task-specific model routing (e.g. cheaper model for `back` generation, stronger for extraction)
- Automatic fallback between providers
- Cost tracking dashboard
- Support for non-OpenAI-compatible local servers that do not speak the OpenAI chat completions API
