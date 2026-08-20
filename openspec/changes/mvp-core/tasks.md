# Tasks: MVP Core — SpacedBro

## 1. Project setup
- [ ] Initialize Python project (poetry / uv / requirements)
- [ ] Add aiogram 3.x, OpenAI client (vision), SQLAlchemy/asyncpg, Redis client
- [ ] Env vars only: `BOT_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL`, `REDIS_URL` (never commit secrets)
- [ ] Structure: handlers, services, models, srs, llm, media, scheduler, profile
- [ ] Logging + user-facing error helpers
- [ ] Runtime choice: long polling **or** webhook; document how to run + optional Docker

## 2. Database & models
- [ ] Schema: users (target_lang, ui_lang, level_estimate, activity UTC fields), learning_items (unique front per user), optional activity_events
- [ ] User repository: get_or_create, update activity, update level estimate, language change flow state
- [ ] LearningItem repository: CRUD, due query, duplicate check (normalized front), boost/reset SRS
- [ ] Migrations

## 3. LLM layer
- [ ] Text extraction (structured candidates)
- [ ] Image extraction (multimodal → same structure)
- [ ] **Cheap `back` prompt** (one-line translation/definition, mini model)
- [ ] Generation: confirm, example, review package, boost offer, language-change copy, friendly errors
- [ ] Character + UI language adaptation in system prompt
- [ ] Token/image usage logging

## 4. Telegram handlers
- [ ] `/start` — welcome (English default), optional ask target language, invite first item
- [ ] Text handler → extract → candidates + buttons
- [ ] Photo handler → extract → same flow
- [ ] Callbacks: Add / Skip / Boost / language confirm (double step) / review quality / Show answer / Example
- [ ] Optional `/review` or NL review request
- [ ] Voice → short "not supported yet"
- [ ] Global error handler → short friendly message

## 5. Core learning flow
- [ ] Add item (fill `back` via cheap LLM)
- [ ] Duplicate detection → notify + offer Boost
- [ ] Boost resets SRS to frequent schedule
- [ ] On-demand review + rating → update SRS
- [ ] Simple list/status of items (optional command/button)

## 6. User profile & language
- [ ] Default target_lang=`en`, UI English
- [ ] Detect user message language; adapt bot language / mix per design rules
- [ ] Double-confirmation flow to change target language (only one target at a time)
- [ ] Level estimate update heuristics (item count + review quality)

## 7. SRS engine
- [ ] Simplified SM-2 with documented defaults (new interval, quality mapping, growth, caps)
- [ ] Boost/reset API
- [ ] Unit tests for intervals and boost

## 8. Scheduler / proactive
- [ ] Job: due items + UTC activity window
- [ ] Cap **1–3** proactive messages/day by activity
- [ ] Back-off for inactive users

## 9. Polish & observability
- [ ] Consistent SpacedBro tone
- [ ] Metrics/logs: messages, images, adds, duplicates, boosts, reviews, LLM errors
- [ ] Local run README (no secrets in repo)
- [ ] Smoke checklist: text add, image add, duplicate+boost, review, proactive dry-run, language change double confirm, error paths

## Definition of Done (MVP)
- Full loop works for text and image.
- Profile stores target language + level signals; UI adapts; language change needs double confirm.
- Duplicates offer boost; proactive ≤1–3/day; errors are short and clear.
- Spec requirements satisfied; engineers can demo without secrets in git.
