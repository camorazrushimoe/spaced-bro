# Tasks: MVP Core — SpacedBro

## 1. Project setup
- [ ] Initialize Python project (poetry / uv / requirements)
- [ ] Add aiogram 3.x, OpenAI client (with vision support), SQLAlchemy / asyncpg (or preferred ORM), Redis client
- [ ] Configure environment variables (BOT_TOKEN, OPENAI_API_KEY, DATABASE_URL, REDIS_URL)
- [ ] Basic project structure: handlers, services, models, srs, llm, media, scheduler
- [ ] Logging and simple error handling

## 2. Database & models
- [ ] Create PostgreSQL schema: users, learning_items (and optional activity_events)
- [ ] Implement User repository (get_or_create by telegram_id, update last_active)
- [ ] Implement LearningItem repository (CRUD, due items query, update SRS state)
- [ ] Migrations (Alembic or equivalent)

## 3. LLM layer
- [ ] Intent & extraction prompt + structured output for **text**
- [ ] Intent & extraction for **images** (multimodal / vision call → same candidate structure)
- [ ] Generation helpers: confirm message, example sentence, review prompt, "show answer"
- [ ] Character system prompt (SpacedBro tone)
- [ ] Token / image usage logging + basic cost guardrails

## 4. Telegram handlers
- [ ] /start — welcome + short explanation + first action prompt
- [ ] Text message handler → extract intent → reply with candidates + buttons
- [ ] **Photo handler** → download image → multimodal extraction → same candidate + buttons flow
- [ ] Callback handlers: Add / Skip / Confirm, Review quality (Again/Hard/Good/Easy or simplified), Show answer, Request example
- [ ] Ensure all replies are short and use inline keyboards where useful

## 5. Core learning flow
- [ ] Add item flow (with confirmation when needed) — shared for text and image origins
- [ ] List / status of current learning items (simple command or button)
- [ ] On-demand review: user can request "review" / "потренируемся"
- [ ] After review answer → update SRS state → schedule next

## 6. SRS engine
- [ ] Implement simplified SM-2 (new → learning → review intervals)
- [ ] Quality rating mapping
- [ ] Query due cards for a user
- [ ] Unit tests for interval calculations

## 7. Scheduler / proactive
- [ ] Background job that finds due cards + users in convenient windows
- [ ] Simple activity-based window (e.g. hours when user was active in last N days)
- [ ] Rate limits per user (max proactive messages / day)
- [ ] Graceful back-off for inactive users

## 8. Polish & observability
- [ ] Consistent SpacedBro tone across all messages
- [ ] Basic metrics / logs (messages processed, images processed, cards added, reviews completed, LLM errors)
- [ ] README for running locally + required secrets
- [ ] Smoke test script or manual checklist for the full loop (text + image)

## Definition of Done (MVP)
- User can /start, send a word **or an image**, confirm addition, receive a review (on-demand or proactive), rate it, and see the next interval updated.
- Bot replies stay short and use buttons.
- Spec requirements in this change are satisfied.
- Engineers can demo the happy path for both text and image inputs.
