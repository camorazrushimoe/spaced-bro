# Proposal: LLM Router & Environment Configuration

## Why

SpacedBro needs a clean way to switch LLM providers and models across environments without changing application code.

We have three clusters:
- **development / local** — cheap or free local model (Gemma 4) for pipeline testing and prompt iteration
- **preprod** — production-like quality at lower risk
- **production** — reliable, cost-efficient cloud model with vision support

Hard-coding the model or scattering provider logic across handlers is fragile. We need a single configuration surface and a thin abstraction so the rest of the system only talks to a stable interface.

## Scope

**In scope:**
- Environment-driven configuration (`APP_ENV` or equivalent)
- Unified LLM client interface (text + vision)
- Default mapping:
  - `development` → local OpenAI-compatible endpoint (Gemma 4 via Ollama / vLLM / LM Studio etc.)
  - `preprod` / `production` → OpenAI `gpt-5.6-luna` (vision-capable, cost-efficient)
- All secrets and endpoints via environment variables only
- Soft per-user rate limits remain the responsibility of the calling layer (already specified in mvp-core)
- Ability to override model / base URL / provider for any environment via env for flexibility

**Out of scope:**
- Fine-grained routing by task type (extraction vs generation) — can be added later if needed
- Multiple simultaneous providers with fallback chains (keep simple for MVP)
- Embedding models, TTS, STT

## Success criteria

- Changing `APP_ENV` (or explicit LLM_* overrides) switches the backend without code changes
- Local Gemma works for the full core loop in development
- Production uses `gpt-5.6-luna` by default
- Specs and design make the contract clear for engineers implementing the client
- The resolved backend and the entire error/retry contract are assertable in unit tests with a stub transport (no real provider needed)

## Decisions locked (per QA review of PR #6)

The QA review (NEEDS CHANGES) demanded that configuration resolution, error
mapping, and the vision/structured-output failure surface be fully pinned. These
decisions are now normative in `specs/llm-client/spec.md`:

1. **Exact dev defaults (B1).** `development` resolves to
   `(openai_compatible, http://localhost:11434/v1, gemma-4-e2b-it, api_key="local-dev")`
   — one base URL, one model, one api-key sentinel. Other local servers/tags are
   selected by explicit override; there is no second "configurable default" knob.
2. **Partial-override composition rule (B1).** `LLM_PROVIDER` / `LLM_MODEL` /
   `LLM_BASE_URL` compose **per variable**: each set variable replaces only its
   own component of the resolved tuple, unset components keep the `APP_ENV`
   default, mixed states are legal. `OPENAI_API_KEY` is outside the rule — it is
   consumed only when the *resolved* provider is `openai`.
3. **Fail-fast on bad configuration (B1).** Missing/empty/unknown `APP_ENV` and
   any `LLM_PROVIDER` outside `openai|openai_compatible` abort startup with exit
   code 78 and an `ERROR` log naming the variable and allowed values — never a
   silent production-like default. The key check applies to the *resolved*
   provider (not the environment), and "empty" includes whitespace-only.
4. **Failure → domain-error mapping table (B2).** Every failure (timeout,
   connection refused/DNS, 429, 401/403, 400/404, 5xx, malformed 200 body,
   malformed or schema-mismatched structured JSON, vision-unsupported, unknown)
   maps to exactly one of the five domain errors; the table is normative and each
   row is a stub-transport test case.
5. **Retryable set + backoff (B2).** `LLM_MAX_RETRIES` defaults to 2
   (≤3 total attempts); 0 disables, negative fails fast. Retryable: `timeout`,
   `rate_limit`, 5xx-caused `provider_unavailable`. Never retried:
   `invalid_response`, `vision_not_supported`, connection-level
   `provider_unavailable`. Backoff is exponential full-jitter
   `[0, min(10s, 1s * 2^attempt)]` with `Retry-After` honoured; the clock is
   injectable for boundary tests.
6. **Stub-transport test seam is a SHALL (B2).** The client MUST be constructable
   with an injected transport; all contract tests (every error and retry path)
   MUST run against a stub, without a real provider, network, or local model
   server.
7. **Vision-unsupported is a first-class domain error (B3).**
   `vision_not_supported` joins the domain set (now five classes). A local
   vision failure with a distinguishing signal raises it; without one it
   conservatively raises `provider_unavailable`. Callers MUST treat the two
   differently (permanent environment message vs transient message). Scenarios
   cover local vision failure (both signals) and structured-output failure
   (malformed JSON / schema mismatch → `invalid_response`, no retry).
