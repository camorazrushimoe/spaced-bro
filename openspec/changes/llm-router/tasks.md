# Tasks: LLM Router & Environment Configuration

## 1. Configuration layer
- [ ] Define `APP_ENV` values and default resolution logic (development → local, preprod/production → gpt-5.6-luna)
- [ ] Support explicit overrides: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `OPENAI_API_KEY`, timeout/retries
- [ ] Fail fast at startup if production/preprod is selected but `OPENAI_API_KEY` is missing
- [ ] Log selected provider + model at startup

## 2. Client abstraction
- [ ] Introduce `LLMClient` protocol / interface with `complete` and `complete_with_vision`
- [ ] Implement OpenAI provider (official SDK or httpx) for `gpt-5.6-luna`
- [ ] Implement OpenAI-compatible provider for local endpoints (Ollama / LM Studio / vLLM)
- [ ] Structured output support (JSON schema / response_format)
- [ ] Configurable timeout and limited retries
- [ ] Clear error types (rate limit, timeout, invalid response, provider error)

## 3. Integration points
- [ ] Wire the client into the existing extraction / generation call sites from mvp-core
- [ ] Ensure vision path works with both providers (or degrades gracefully on local if model lacks vision)
- [ ] Keep soft per-user vision rate limit at the application layer (already specified)

## 4. Developer experience
- [ ] Document in README / local setup how to run Gemma 4 (E2B or E4B) via Ollama or equivalent and point the bot at it
- [ ] Example `.env.example` with all relevant variables
- [ ] Smoke test / checklist: text extraction + vision extraction + back generation on both local and OpenAI

## Definition of Done
- Changing only environment variables switches between local Gemma and `gpt-5.6-luna`
- Core loop (text + image → candidates → back) works against both backends
- No provider-specific types leak into handlers
- Specs and design are the source of truth for the contract
