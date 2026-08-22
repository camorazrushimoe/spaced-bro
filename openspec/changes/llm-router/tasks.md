# Tasks: LLM Router & Environment Configuration

## 1. Configuration layer
- [x] Define `APP_ENV` values and default resolution logic (development → local, preprod/production → gpt-5.6-luna)
- [x] Support explicit overrides: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `OPENAI_API_KEY`, timeout/retries
- [x] Fail fast at startup if production/preprod is selected but `OPENAI_API_KEY` is missing
- [x] Log selected provider + model at startup

## 2. Client abstraction
- [x] Introduce `LLMClient` protocol / interface with `complete` and `complete_with_vision`
- [x] Implement OpenAI provider (official SDK or httpx) for `gpt-5.6-luna`
- [x] Implement OpenAI-compatible provider for local endpoints (Ollama / LM Studio / vLLM)
- [x] Structured output support (JSON schema / response_format)
- [x] Configurable timeout and limited retries
- [x] Clear error types (rate limit, timeout, invalid response, provider error)

## 3. Integration points
- [x] Wire the client into the existing extraction / generation call sites from mvp-core
- [x] Ensure vision path works with both providers (or degrades gracefully on local if model lacks vision)
- [ ] Keep soft per-user vision rate limit at the application layer (already specified)

## 4. Developer experience
- [x] Document in README / local setup how to run Gemma 4 (E2B or E4B) via Ollama or equivalent and point the bot at it
- [x] Example `.env.example` with all relevant variables
- [x] Smoke test / checklist: text extraction + vision extraction + back generation on both local and OpenAI

## Definition of Done
- Changing only environment variables switches between local Gemma and `gpt-5.6-luna`
- Core loop (text + image → candidates → back) works against both backends
- No provider-specific types leak into handlers
- Specs and design are the source of truth for the contract
