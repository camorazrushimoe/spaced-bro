# Design: MVP Core — SpacedBro

## Architecture overview

```
Telegram User
      │
      ▼
Telegram Gateway (aiogram, long polling)
  - text / photo / callbacks
  - inline keyboards
      │
      ├──► Media (images → multimodal LLM)
      ├──► Intent & Extraction (LLM)
      ├──► Generation (LLM, language-aware)
      ▼
Core: User Profile · Learning Items · SRS Engine
      │
      ▼
In-process APScheduler (UTC) · proactive 1–3/day
      │
SQLite (single file on volume)
```

Single long-running process. No Redis/Celery in MVP.

## 1. Telegram Gateway
- aiogram 3.x, **long polling** (simpler than webhook for MVP).
- Entry: `/start`, text, photo, `/review` (optional) + NL review, callbacks.
- **Callback idempotency:** each actionable callback carries a unique `callback_id` (or item_id + action + nonce). Processing the same id twice is a no-op (or re-sends the same short ack). Protects double-tap and Telegram redelivery.
- Short replies; buttons for actions.

## 2. Images
- Download → multimodal LLM → 1–3 candidates → same add path as text.
- Process-and-discard; do not store images permanently.

## 3. Intent & extraction
- Structured candidates: `front`, optional `context`.
- **Non-learning text** (greeting, thanks, off-topic): short ack + hint to send a word/photo or `/review`. Do **not** create candidates.
- Learning request (explicit or extractable vocab): candidates + buttons.

## 4. User profile
Per `telegram_id`:
- `native_lang` — language of explanations/`back` (default **`ru`**). Set at onboarding or default.
- `target_lang` — only language being learned (default **`en`**). Change = **double confirmation**.
- `ui_lang` / detected — default English UI; adapt/mix per heuristics.
- `level_estimate` — beginner | intermediate | advanced (heuristic from item count + review quality).
- `created_at`, `last_active_at` (UTC), `activity_hours_utc` (24-bucket counts).
- `proactive_count_date` (UTC date) + `proactive_count` for daily cap.

**Language rules (MVP, soft heuristics — not strict gates):**
1. Default UI English.
2. If user writes in another language → reply in that language or mix.
3. Higher level → prefer more target-language UI.
4. Struggles → allow mix.
5. Example strong signal: ≥100 items and mostly Good/Easy → prefer full target UI.

Onboarding: **SHALL ask once** for target language (default English if skipped) and use default `native_lang=ru` unless user states otherwise later.

## 5. Learning items & add flow
1. Candidate `front` (normalized).
2. If duplicate → notify + **Boost** offer; do not create second row.
3. Else cheap LLM: one-line `back` **into `native_lang`**.
4. **Show `front` + `back` to user** with buttons: [Save] [Wrong — regenerate] [Skip].
5. Only on Save → persist card with SRS new state.

**Front normalization (exact):**
```
normalized = " ".join(front.casefold().split())
```
Unique per (`user_id`, `normalized_front`).

**SRS fields:** `ease` (float), `interval_minutes` (int — supports sub-day), `repetitions` (int), `next_review_at` (UTC), `last_review_at`, `status`.

Do **not** use `interval_days` as the sole interval unit.

## 6. SRS Engine — fixed mapping (deterministic)

**Clock:** all computations take an injected `now: datetime` (UTC). Production uses real UTC now; tests freeze the clock.

**New / Boost state:**
- `repetitions = 0`
- `ease = 2.5`
- `interval_minutes = 20`
- `next_review_at = now + 20 minutes`
- `status = learning`

**Quality → update** (after reveal/rate):

| Quality | Effect |
|---------|--------|
| **Again** | `repetitions = 0`; `interval_minutes = 10`; `ease = max(1.3, ease - 0.2)`; `status = learning` |
| **Hard** | `interval_minutes = max(10, int(interval_minutes * 1.2))`; `ease = max(1.3, ease - 0.15)`; `repetitions += 1` |
| **Good** | if `repetitions == 0`: `interval_minutes = 1440` (1 day); elif `repetitions == 1`: `interval_minutes = 4320` (3 days); else: `interval_minutes = int(interval_minutes * ease)`; `ease` unchanged; `repetitions += 1`; `status = review` when interval ≥ 1440 |
| **Easy** | same as Good but `interval_minutes = int(interval_minutes * ease * 1.3)` after the first two steps; `ease = ease + 0.15` |

**Max interval:** `interval_minutes` capped at **259200** (180 days).

**Boost:** set state equal to New / Boost state above; keep `front`/`back`/`context`.

Pure function: `(state, quality, now) → new_state`. Unit-test without Telegram/LLM.

## 7. Review session & backlog
- On-demand `/review` or NL: bot reports **how many due** (`next_review_at <= now`), then presents **one** card at a time (front → show answer → quality).
- After rating, offer next due or stop.
- **Unattended due cards:** remain due; no extra penalty. Proactive may surface up to daily cap; rest wait for on-demand or later days.
- Proactive and on-demand share the same due queue; proactive does not remove the need for on-demand when backlog > cap.

## 8. Scheduler / proactive
- **In-process APScheduler** in the same process as the bot (single instance).
- Job every N minutes: find users with due items, in window, under cap.
- **Day** = calendar UTC date (reset at **UTC midnight**).
- Cap: **1** if low activity, **2** medium, **3** high (simple: 0 messages in last 7 days → 1; else if active ≥3 distinct UTC hours in last 7 days → 3; else 2).
- **On-demand reviews do not increment** proactive_count.
- **Cold start (no histogram):** allow proactive only in **09:00–21:00 UTC**.
- **Back-off:** if `last_active_at` < now - 14 days → skip proactive.
- Document single-instance assumption (no distributed lock in MVP).

## 9. Errors (user-facing)
- LLM/API failure → short retry message; **do not** save card if `back` generation failed.
- Bad JSON from LLM → short retry; no partial card.
- Unreadable image → short message.
- Voice → **MUST** reply: voice not supported yet; send text or photo.
- No stack traces to users.

## 10. Runtime & ops
- **Docker Compose required** for factory: service `bot` + named volume for SQLite file.
- Long polling; **HEALTHCHECK** (e.g. process up + SQLite openable, or HTTP `/healthz` if a tiny health server is added).
- Env: `BOT_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL` (e.g. `sqlite:////data/spacedbro.db`).
- **Alembic** migrations; apply on startup or explicit migrate step before traffic.
- Postgres/Redis: out of MVP; upgrade path later if multi-instance needed.

## Data model (sketch)

**users:** telegram_id, native_lang, target_lang, ui_lang, level_estimate, created_at, last_active_at, activity_hours_utc, proactive_count, proactive_count_date

**learning_items:** id, user_id, front, normalized_front, back, context, ease, interval_minutes, repetitions, next_review_at, last_review_at, status, created_at; UNIQUE(user_id, normalized_front)

## LLM guidelines
- Mini model for `back` and short copy; vision only for images.
- Soft per-user rate limit recommended (e.g. max N vision calls/hour) — implement as simple counter; exact N configurable (default 20).

## Non-goals
Voice/STT, multi-worker Celery, multi target languages, Anki import, full dashboard.
