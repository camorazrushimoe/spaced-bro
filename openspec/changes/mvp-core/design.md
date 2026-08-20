# Design: MVP Core — SpacedBro

## Architecture overview

```
Telegram User
      │
      ▼
Telegram Gateway (aiogram)
  - message handlers (text, callback_query)
  - inline keyboards
  - outgoing messages (reply + proactive)
      │
      ├──► Intent & Extraction (LLM)
      │      extracts candidates / intent
      │
      ├──► Conversation / Generation (LLM)
      │      short bro-style replies, examples
      │
      ▼
Core Services
  ├── User Memory
  ├── Learning Items (cards)
  └── SRS Engine
      │
      ▼
Scheduler / Proactive Layer
  - background jobs / queue
  - activity window heuristics
```

## Key components

### 1. Telegram Gateway
- Framework: **aiogram 3.x**
- Handles: `/start`, plain text, callback queries from inline buttons.
- All user-facing messages must be short. Prefer buttons for binary / choice actions.
- Supports both reply and proactive (bot-initiated) messages.

### 2. Intent & Extraction (LLM)
- Input: raw user text.
- Output: structured intent + list of candidate learning items (word or short phrase + optional translation/context).
- Cases:
  - Explicit: "запомни ubiquitous", "add the word 'resilient'".
  - Implicit / multi-candidate: user pastes a sentence → bot suggests 1–3 useful items with buttons.
- Always confirm before permanent add when ambiguous.

### 3. User Memory (minimal)
Store only what is needed:
- `telegram_id` (primary key)
- `created_at`, `last_active_at`
- activity log or derived preferred time windows (simple histogram of hours/days)
- settings: target language (default English), source language (default Russian), timezone if available
- optional: rough language level (future)

No long conversation history required for MVP. Keep context window small.

### 4. Learning Items
Each item ("card"):
- `id`, `user_id`
- `front` (word / phrase to learn)
- `back` (translation / meaning / short definition)
- `context` (optional original sentence or note)
- `language_pair` (e.g. en-ru)
- SRS state: `ease`, `interval_days`, `repetitions`, `next_review_at`, `last_review_at`, `status` (new / learning / review)

### 5. SRS Engine
- Algorithm: **simplified SM-2**
  - New card → first review soon (minutes/hours configurable).
  - Quality rating (at minimum: Again / Hard / Good / Easy) or simplified binary + quality.
  - Update interval and ease accordingly.
- Select due items for a user.
- After user responds to a review, update the card state immediately.

### 6. Conversation / Generation (LLM)
- System prompt encodes SpacedBro character: short, concrete, friendly bro, no teacher tone, light slang allowed.
- Tasks:
  - Confirm addition.
  - Generate 1 short example sentence for a word.
  - Package a review ("Here's *ubiquitous*. Can you use it?" + buttons).
  - Handle "I don't know" / "show answer" flows.
- Responses must stay brief (ideally < 2–3 short sentences + buttons).

### 7. Scheduler / Proactive
- Background worker (APScheduler, Celery, or aiogram + Redis queue).
- Periodically finds users with due cards and whose current time falls into a "convenient window" derived from past activity.
- Sends at most a limited number of proactive reviews per day per user (configurable, e.g. 3–5).
- Never spam. If user is inactive for a long time, back off.

## Data model (sketch)

**users**
- telegram_id (PK)
- username, first_name (optional)
- source_lang, target_lang
- timezone
- created_at, last_active_at
- preferred_hours (json or derived)

**learning_items**
- id (PK)
- user_id (FK)
- front, back, context
- ease, interval_days, repetitions
- next_review_at, last_review_at
- status, created_at

**activity_events** (optional, for patterns)
- user_id, event_type, created_at

## LLM usage guidelines
- Prefer structured output (JSON mode / tool calling) for extraction.
- Keep generation prompts tight; force short answers.
- Cost control: use mini-class model; cache nothing sensitive; log token usage.

## Security & privacy
- Store only Telegram ID and learning data.
- No sharing of user vocabulary between users.
- API keys via environment / secrets manager.
- Rate-limit LLM and Telegram calls.

## Non-goals for this design
- Perfect NLP without LLM.
- Full Anki compatibility / import-export (future).
- Multi-device sync beyond Telegram.
