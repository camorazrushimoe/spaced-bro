# Tasks: MVP Core — SpacedBro

## 1. Project setup
- [x] Python project + aiogram 3.x + OpenAI client + SQLAlchemy/Alembic
- [x] Env: `BOT_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL` (sqlite)
- [x] **Dockerfile + compose.yml** (bot service, volume for SQLite, healthcheck)
- [x] Long polling entrypoint + in-process APScheduler
- [x] Injectable clock utility

## 2. Database
- [x] SQLite schema: users (native_lang, target_lang, …), learning_items (normalized_front unique, interval_minutes)
- [x] Alembic migrations; migrate on deploy/start
- [x] Repositories: users, items, due query, duplicate check, boost, proactive counters

## 3. LLM
- [ ] Text extraction + non-learning empty path
- [ ] Image extraction
- [ ] Cheap `back` into native_lang
- [ ] Copy: confirm, regenerate, boost, errors, voice reply
- [ ] Optional soft per-user vision rate limit (default 20/hour)

## 4. Handlers
- [ ] `/start` + ask target language
- [ ] Text / photo flows
- [ ] Confirm-back Save / Regenerate / Skip
- [ ] Callback idempotency
- [ ] Duplicate + Boost
- [ ] Review session with due count
- [ ] Voice MUST short reply
- [ ] Global friendly errors

## 5. SRS
- [ ] Implement exact design §6 mapping
- [ ] Unit tests with frozen clock (Again/Hard/Good/Easy, boost, cap)

## 6. Scheduler
- [ ] APScheduler job; UTC midnight cap; cold-start 09–21 UTC; 14-day back-off
- [ ] Cap scaling 1/2/3; on-demand excluded

## 7. Polish
- [ ] Logs/metrics; local README; smoke checklist including confirm-back, non-learning text, duplicate+boost, review session, proactive dry-run

## Definition of Done
Happy path text+image with confirm-back; SRS unit tests green; compose up; secrets only in env; issue #3 blocking items addressed in spec.
