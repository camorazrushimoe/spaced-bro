# Design: MVP Core — SpacedBro

## Architecture overview

```
Telegram User
      │
      ▼
Telegram Gateway (aiogram)
  - message handlers (text, photo, callback_query)
  - inline keyboards
  - outgoing messages (reply + proactive)
      │
      ├──► Media (Images)
      │      download photo → multimodal LLM
      │
      ├──► Intent & Extraction (LLM)
      │      extracts candidates / intent from text or image
      │
      ├──► Conversation / Generation (LLM)
      │      short bro-style replies, examples, language-adapted
      │
      ▼
Core Services
  ├── User Profile / Memory
  ├── Learning Items (cards)
  └── SRS Engine (+ boost/reset)
      │
      ▼
Scheduler / Proactive Layer
  - background jobs / queue
  - UTC activity-window heuristics
  - 1–3 proactive messages / day max
```

## Key components

### 1. Telegram Gateway
- Framework: **aiogram 3.x**
- Handles: `/start`, plain text, photos, callback queries.
- Entry points: `/start`, text, photo, optional `/review` or natural-language review request, callbacks for Add/Skip/Boost/Confirm language/rate quality.
- All user-facing messages short; prefer buttons for actions.
- Supports reply and proactive (bot-initiated) messages.

### 2. Image handling (MVP)
- User sends a photo → download → multimodal LLM.
- Extract 1–3 candidate learning items → same confirmation path as text.
- Prefer process-and-discard images (do not store permanently).

### 3. Intent & Extraction (LLM)
- Input: text or image.
- Output: structured candidates (`front`, optional context).
- On add: fill `back` via a **cheap, short translation/definition prompt** (mini model, minimal tokens).
- Confirm before permanent add when ambiguous.

### 4. User profile (client profile module)
Store per `telegram_id`:
- `target_lang` — the **only** language being learned (default: `en`). Change requires **double confirmation** (propose → confirm again).
- `ui_lang` / detected communication language — default bot replies in **English**; if user consistently writes in another language, adapt replies to that language (or mix).
- `level_estimate` — rough proficiency in target language (e.g. beginner / intermediate / advanced), updated from signals:
  - number of items in dictionary (e.g. 100+ stable items → treat as strong)
  - review quality over time (many Easy/Good at long intervals)
  - whether user struggles in target-language UI
- `created_at`, `last_active_at`, activity hour histogram in **UTC**
- optional: `first_name`, `username`

**Language adaptation rules:**
1. Default UI language: English.
2. If user writes in another language → bot may reply in that language or mix.
3. As `level_estimate` rises, prefer more (or fully) target-language UI.
4. If user struggles (failed reviews, asks for help in other language), allow mixing.
5. Heuristic example: ~100+ solid items at C1-ish stability → fully target language for bot UI when reasonable.

Onboarding: bot MAY ask once what language the user wants to learn; default English if skipped.

### 5. Learning items
- `front`, `back` (from cheap LLM), optional `context`, `language_pair`
- SRS fields: ease, interval, repetitions, next_review_at, last_review_at, status
- **Duplicates:** if `front` (normalized) already exists for user → do not create second card; reply that it is already in the dictionary and offer **Boost** (reset schedule as if newly added / short interval again).

### 6. SRS Engine (simplified SM-2 + boost)
Suggested defaults (tunable):
- New card: first review in ~10–30 minutes (or next convenient window).
- Qualities: Again / Hard / Good / Easy.
- Again → short interval (minutes–hours), reset or reduce repetitions.
- Good/Easy → increase interval (e.g. 1d → 3d → 7d → 14d → 30d → 90d… with ease factor).
- Cap max interval (e.g. 180 days) if desired.

**Boost (speed up learning):**
- When user hits a duplicate or explicitly boosts a forgotten card (e.g. was on 90-day interval and does not remember):
  - Reset to "new/learning" short interval schedule (as if just added).
  - Keep the same card identity and `back`/context.

### 7. Conversation / Generation (LLM)
- System prompt: SpacedBro tone + current `ui_lang` / mix policy + target language.
- Tasks: confirm add, example sentence, review packaging, show answer, language-change confirmation, boost offer, friendly errors.
- Keep replies ≤2–3 short sentences + buttons.
- Translation prompt for `back`: minimal — "Give a short definition/translation of {front} into {user_native_or_ui} for a language learner. One line only."

### 8. Scheduler / Proactive
- Background worker (APScheduler / Celery / Redis queue).
- Activity windows in **UTC** from past message hours; if little data, use a conservative default UTC window.
- **Hard cap: 1–3 proactive messages per user per day**, scaled by activity (less active → closer to 1).
- Back off if user inactive for a long time.
- Never send 10+ messages/day.

### 9. Errors (user-facing)
Keep it simple and clear:
- LLM / API timeout or failure → "Something glitched on my side. Try again in a bit."
- Unreadable image → "Couldn't pull any words from that photo. Try a clearer shot."
- Voice message → "Voice isn't supported yet — send text or a photo."
- Generic unexpected → short apology + invite retry.
No stack traces to the user.

### 10. Runtime & ops
- **Recommended:** one long-running process (aiogram long polling) + in-process or sidecar scheduler; **or** webhook + worker.
- Docker optional but useful for factory deploy.
- Secrets (`BOT_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL`, `REDIS_URL`) **only via environment / secret store — never commit to git**.
- Product owner provides tokens to developers out of band.

## Data model (sketch)

**users**
- telegram_id (PK)
- username, first_name (optional)
- target_lang (default `en`)
- ui_lang / detected_lang
- level_estimate
- created_at, last_active_at
- activity_hours_utc (json or derived)
- proactive_sent_today / last_proactive_date

**learning_items**
- id, user_id
- front (unique per user, normalized)
- back, context
- ease, interval_days, repetitions
- next_review_at, last_review_at, status
- created_at

**activity_events** (optional)
- user_id, event_type, created_at (UTC)

## LLM usage guidelines
- Structured output for extraction.
- Mini-class model for translation/`back` and short replies.
- Vision model only for images; resize if needed.
- Log token usage; avoid long chat history in prompts.

## Security & privacy
- Minimal PII; no sharing across users.
- Images not stored permanently by default.
- Secrets outside the repository.

## Non-goals
- Voice/STT, multi-target languages in parallel, Anki import, full dashboard.
