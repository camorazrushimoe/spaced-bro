# Proposal: MVP Core — SpacedBro

## Why

We need a working Telegram companion bot that helps users learn a target language (default: English) through spaced repetition. The bot must feel like a short, concrete "bro" helper rather than a textbook or teacher.

This change defines the **MVP** that engineers can estimate and implement. It delivers the core loop:

1. User sends a word / phrase / short text **or an image** (screenshot, photo of a word/phrase).
2. Bot understands (or suggests) what to learn and adds it to the personal dictionary.
3. Bot schedules and delivers reviews using spaced repetition, packaged in a short, interesting way.
4. Bot stays brief, uses buttons for actions, adapts language to the user, and respects low message volume (1–3 proactive/day).

## Scope (MVP)

**In scope:**
- Text messages (words, phrases, short text).
- **Images / photos / screenshots** — multimodal LLM extracts candidate words/phrases.
- Intent detection + candidate extraction via LLM (text and image).
- Add / confirm learning items with inline buttons.
- Personal learning dictionary per user (one target language at a time).
- **User profile**: target language to learn, estimated proficiency level, activity patterns.
- Bot UI language: default English; adapt when user writes in another language; mix or fully switch based on level.
- Change of target language only with **double confirmation**.
- SRS engine based on a simplified SM-2 algorithm + **boost/reset interval** when user re-encounters a forgotten long-interval card.
- Duplicate handling: inform user item already exists; offer to boost learning (reset schedule).
- Review delivery: on-demand + basic proactive messages (UTC activity heuristic).
- Short responses in SpacedBro character + example sentences / simple packaging.
- `back` (translation/meaning) filled by a **cheap LLM prompt** when adding.
- Graceful, user-friendly error messages (LLM/API/image failures).
- PostgreSQL + Redis (recommended), Python + aiogram 3.x + OpenAI (vision-capable, cost-efficient).
- Runtime: long polling or webhook; single service + background scheduler; secrets via env (not in repo).

**Out of scope for MVP (Phase 2+):**
- Voice messages / STT.
- Advanced ML-based activity models.
- Learning multiple target languages in parallel.
- Rich packaging (movie quotes, full mini-dialogues).
- Streaks, gamification, analytics dashboard.
- Web / mobile clients.

## Success criteria

- User can `/start`, set/confirm target language, send text or image, confirm addition (with translation from LLM), handle duplicates/boost, receive reviews (on-demand or proactive ≤1–3/day), rate them, and see SRS update.
- Bot language adapts to user level and input language.
- Replies stay short and actionable.
- Spec is clear enough for estimation and implementation without major clarification rounds.

## Decisions locked for MVP

| Topic | Decision |
|-------|----------|
| Bot name | SpacedBro |
| Bot UI language | Default English; adapt to user's language; mix/full target based on estimated level |
| Target language | One at a time; stored in profile; change requires double confirmation |
| Level tracking | Estimate from vocabulary size / review quality; aim to fully use target language when strong (e.g. 100+ solid items) |
| Input channels | Text + Images (voice deferred) |
| Translation (`back`) | Cheap LLM prompt on add |
| Duplicates | Notify + offer boost (reset SRS to frequent reviews) |
| Proactive volume | 1–3 messages/day depending on user activity; never flood |
| Time windows | UTC-based activity heuristic |
| SRS | Simplified SM-2 + boost/reset |
| Errors | Short friendly message + retry invitation |
| Runtime | Long polling or webhook; process + scheduler; secrets in env only |
| LLM | OpenAI with vision, cost-efficient |
| Stack | Python, aiogram 3.x, PostgreSQL, Redis recommended |
