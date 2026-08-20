# Spec: LLM Client

## Requirements

### Configuration

- The system SHALL determine the active LLM backend from environment variables.
- The system SHALL support at least the following `APP_ENV` values: `development`, `preprod`, `production`.
- When `APP_ENV=development` and no explicit `LLM_*` overrides are present, the system SHALL default to a local OpenAI-compatible endpoint suitable for Gemma 4.
- When `APP_ENV` is `preprod` or `production` and no explicit overrides are present, the system SHALL default to OpenAI model `gpt-5.6-luna`.
- Explicit environment variables (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `OPENAI_API_KEY`) SHALL take precedence over `APP_ENV` defaults.
- In `preprod` and `production` the system SHALL refuse to start if `OPENAI_API_KEY` is missing or empty.
- The system SHALL log the resolved provider and model name at startup.

### Client contract

- The application SHALL interact with the LLM only through a single client abstraction.
- The client SHALL support text-only completion with optional structured output.
- The client SHALL support vision (image + text) completion with optional structured output.
- The client SHALL expose configurable timeout and a small number of retries.
- Provider-specific types and error details SHALL NOT leak into the rest of the application; errors SHALL be mapped to a small set of domain errors (timeout, rate limit, invalid response, provider unavailable).

### Behaviour across environments

- The same application code path SHALL be used for extraction, back generation and short replies regardless of which backend is active.
- When the local model does not support vision, a vision request SHALL fail with a clear, catchable error so the caller can respond appropriately to the user.

## Scenarios

### Happy path — production

- GIVEN `APP_ENV=production` and a valid `OPENAI_API_KEY`
- WHEN the bot needs to generate a `back` or extract candidates
- THEN the request is sent to `gpt-5.6-luna` via the official OpenAI endpoint

### Happy path — local development

- GIVEN `APP_ENV=development` and a running local OpenAI-compatible server with Gemma 4
- WHEN the same code path is exercised
- THEN the request is sent to the local endpoint and a response is returned

### Override

- GIVEN any `APP_ENV` and explicit `LLM_BASE_URL` + `LLM_MODEL`
- WHEN the client is initialised
- THEN the explicit values are used and the `APP_ENV` default is ignored

### Missing key in production

- GIVEN `APP_ENV=production` and no `OPENAI_API_KEY`
- WHEN the process starts
- THEN it exits with a clear error and does not accept traffic
