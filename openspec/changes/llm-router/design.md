# Design: LLM Router & Environment Configuration

## Goals

Provide a single, environment-aware LLM access point that the rest of SpacedBro uses for:
- Intent extraction (text)
- Vision extraction (images)
- Short `back` generation
- Characterful short replies

The implementation must be swappable by configuration only.

## Environment model

Primary signal: `APP_ENV`

| Value          | Meaning                          | Default LLM behaviour                          |
|----------------|----------------------------------|------------------------------------------------|
| `development`  | Local / CI / developer machine   | Local OpenAI-compatible server (Gemma 4)       |
| `preprod`      | Staging / pre-production         | OpenAI `gpt-5.6-luna`                          |
| `production`   | Live traffic                     | OpenAI `gpt-5.6-luna`                          |

Any of these defaults can be overridden by explicit environment variables (see below).

## Configuration (environment variables)

All configuration is read from the environment. No secrets in code or config files committed to the repo.

```
APP_ENV=development|preprod|production

# Explicit overrides (optional — take precedence over APP_ENV defaults)
LLM_PROVIDER=openai|openai_compatible
LLM_MODEL=gpt-5.6-luna|gemma-4-...|...
LLM_BASE_URL=https://api.openai.com/v1|http://localhost:11434/v1|...
OPENAI_API_KEY=...          # required when provider needs a key
LLM_TIMEOUT_SECONDS=30      # optional, default 30
LLM_MAX_RETRIES=2           # optional
```

### Default resolution logic

```
if LLM_PROVIDER / LLM_MODEL / LLM_BASE_URL are set:
    use them
else:
    if APP_ENV == "development":
        provider = openai_compatible
        base_url = http://localhost:11434/v1   # or configurable default for local Gemma
        model    = gemma-4-e2b-it (or whatever the local tag is)
        api_key  = "local" or empty
    else:  # preprod or production
        provider = openai
        base_url = https://api.openai.com/v1
        model    = gpt-5.6-luna
        api_key  = OPENAI_API_KEY (required)
```

Local default base URL and model name SHOULD be documented and overridable so different developers can use Ollama, LM Studio, vLLM, etc.

## Client interface (contract)

A thin abstraction (e.g. `LLMClient`) that the application code depends on.

Required capabilities for MVP:

1. **Text completion / chat** with structured output support (JSON schema or equivalent)
2. **Vision** — accept image bytes / URL + text prompt, return text (and structured data)
3. **Error handling** — distinguish rate-limit, timeout, invalid response, provider errors
4. **Timeout & retry** — configurable, with sensible defaults

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

Implementation details (OpenAI SDK vs raw HTTP, local adapter) live behind this interface.

## Local development expectations

- Developers run a local OpenAI-compatible server (Ollama, LM Studio, vLLM, etc.) with a Gemma 4 variant (E2B or E4B recommended).
- The bot process points at it via `LLM_BASE_URL` + `LLM_MODEL`.
- Vision support in the local model is desirable but not a hard blocker for pure-text path testing. When vision is unavailable locally, the client SHOULD return a clear, catchable error so the application can fall back gracefully (e.g. “image not supported in this environment”).

## Production / preprod expectations

- Default model: `gpt-5.6-luna` (vision-capable, currently the cost-efficient OpenAI option suitable for high-volume short tasks).
- `OPENAI_API_KEY` is mandatory.
- Soft per-user vision rate limit (already defined in mvp-core) continues to apply at the application layer.

## Observability

- Log which provider + model was selected at startup (and on any override).
- Log token usage / latency per call at debug level (or metrics) without logging full prompts/responses in production unless explicitly enabled.

## Non-goals for this change

- Task-specific model routing (e.g. cheaper model for `back` generation, stronger for extraction)
- Automatic fallback between providers
- Cost tracking dashboard
- Support for non-OpenAI-compatible local servers that do not speak the OpenAI chat completions API
