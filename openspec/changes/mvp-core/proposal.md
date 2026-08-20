# Proposal: MVP Core — SpacedBro

## Why

We need a working Telegram companion bot that helps users learn a target language (default: English) through spaced repetition. The bot must feel like a short, concrete "bro" helper rather than a textbook or teacher.

This change defines the **MVP** that engineers can estimate and implement. It delivers the core loop:

1. User sends a word / phrase / short text **or an image**.
2. Bot extracts candidates; on add, generates `back` in the user's **native language**, shows it for confirm/edit, then saves.
3. Bot schedules and delivers reviews via fixed simplified SM-2 + boost.
4. Bot stays brief, adapts language, and sends at most 1–3 **proactive** messages per UTC day.

## Scope (MVP)

**In scope:**
- Text + images (vision LLM); voice deferred (short fixed reply if received).
- Intent extraction; **non-learning text** → short ack, no card.
- Add flow: candidates → generate `back` → **user confirms or rejects `back`** → save.
- Personal dictionary; one `target_lang` at a time; `native_lang` for meanings.
- Double confirmation to change `target_lang`.
- Fixed simplified SM-2 table (all four qualities) + boost/reset.
- Duplicates: notify + Boost; `front` normalized by a defined function.
- Callback idempotency for Add/Boost.
- On-demand review session (due count + one card at a time); unattended due cards simply remain due.
- Proactive 1–3/day (UTC midnight reset); on-demand reviews do **not** count toward that cap.
- **Stack (MVP):** Python + aiogram 3.x + **SQLite** + **in-process APScheduler** + OpenAI (vision). **Docker Compose required** for factory deploy (app service + persistent volume for SQLite). Redis/Postgres **not** required for MVP.
- Injectable clock for tests; secrets via env only.

**Out of scope for MVP (Phase 2+):**
- Voice/STT, Celery/multi-worker, Redis, Postgres (optional later upgrade).
- Multi target languages in parallel, streaks, gamification, rich packaging, web clients.

## Success criteria

- Full loop: text/image → confirm `back` → save → review → rate → SRS update; duplicate+boost; proactive ≤1–3/UTC day; non-learning text handled; unit-testable SRS with frozen clock.

## Decisions locked for MVP

| Topic | Decision |
|-------|----------|
| Bot name | SpacedBro |
| UI language | Default English; adapt/mix by input + level heuristics |
| `native_lang` | Profile field; default `ru` (primary audience); used for `back` |
| `target_lang` | One at a time; default `en`; change = double confirm |
| `back` | Cheap LLM into `native_lang`; **show before save**; user can reject/regenerate |
| Front normalize | `casefold` + strip + collapse internal whitespace |
| SRS | Fixed interval ladder + ease rules below (see design/srs-engine) |
| Qualities | Again / Hard / Good / Easy — all mapped |
| Boost | Reset to new-item schedule; keep content |
| Proactive | 1–3/UTC day; day = UTC midnight; on-demand excluded from cap |
| Cold-start window | Default proactive hours 09:00–21:00 UTC if no history |
| Back-off | No proactive if `last_active_at` older than 14 days |
| Storage | **SQLite** (single file, volume in Compose) |
| Scheduler | **In-process APScheduler**; single instance |
| Deploy | **Docker Compose required**; long polling; healthcheck |
| Migrations | SQLAlchemy + Alembic (works with SQLite) |
| Time | All timestamps UTC; injectable clock for tests |
| Secrets | Env only; owner provides out of band |
