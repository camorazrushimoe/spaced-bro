# Proposal: MVP Core — SpacedBro

## Why

We need a working Telegram companion bot that helps users learn English vocabulary through spaced repetition. The bot must feel like a short, concrete "bro" helper rather than a textbook or teacher.

This change defines the **MVP** that engineers can estimate and implement. It delivers the core loop:

1. User sends a word / phrase / short text.
2. Bot understands (or suggests) what to learn and adds it to the personal dictionary.
3. Bot schedules and delivers reviews using spaced repetition, packaged in a short, interesting way.
4. Bot stays brief, uses buttons for actions, and respects basic activity patterns.

## Scope (MVP)

**In scope:**
- Text messages only (words, phrases, short text).
- Intent detection + candidate extraction via LLM.
- Add / confirm learning items with inline buttons.
- Personal learning dictionary per user.
- Minimal user memory (Telegram ID, activity timestamps, preferred language settings).
- SRS engine based on a simplified SM-2 algorithm.
- Review delivery: on-demand (user asks) + basic proactive messages in likely convenient windows.
- Short responses in SpacedBro character + example sentences / simple packaging.
- PostgreSQL for persistence, Redis for queues/cache (optional but preferred).
- Python + aiogram 3.x + OpenAI (GPT-4o-mini class model).

**Out of scope for MVP (Phase 2+):**
- Voice messages (STT).
- Images / screenshots (Vision / OCR).
- Advanced activity pattern learning (beyond simple time-of-day heuristics).
- Multi-language target support beyond English (as primary).
- Rich packaging (movie quotes, full mini-dialogues, adaptive difficulty).
- User language level estimation, streaks, gamification, analytics dashboard.
- Web / mobile clients.

## Success criteria

- A new user can start a conversation, send a word, confirm addition, and later receive / request a review of that word.
- All bot replies stay short and actionable.
- Engineers can implement and demo the full loop end-to-end.
- Spec is clear enough for effort estimation without major clarification rounds.

## Decisions locked for MVP

| Topic              | Decision                                      |
|--------------------|-----------------------------------------------|
| Bot name           | SpacedBro                                     |
| Primary audience   | Russian-speaking users learning English       |
| Input channels     | Text only                                     |
| SRS algorithm      | Simplified SM-2                               |
| LLM                | OpenAI (cost-efficient model, e.g. GPT-4o-mini) |
| Bot framework      | aiogram 3.x                                   |
| Storage            | PostgreSQL (+ Redis recommended)              |
| Proactive messaging| Basic (activity-window based)                 |
