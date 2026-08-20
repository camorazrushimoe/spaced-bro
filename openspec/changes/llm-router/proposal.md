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
