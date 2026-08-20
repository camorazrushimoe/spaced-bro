# SpacedBro

> Telegram bot companion for language learning with spaced repetition (SRS).
> Short, concrete, in the style of "bro, let's learn this word".

**Repository for specification and development.**  
We use [OpenSpec](https://github.com/Fission-AI/OpenSpec) — lightweight spec-driven development.

## Product Idea

The user sends the bot:
- a word / phrase
- a piece of text
- a voice message
- an image (screenshot, photo of a word)

The bot understands (explicitly or by suggesting options) what exactly the user wants to remember and adds it to the personal learning dictionary.

Later, using the spaced repetition algorithm, it comes back to the user at a convenient time and packages the review in an interesting format:
- example sentence
- short "movie-style" phrase
- comprehension question
- mini-dialogue

The bot learns the user's communication patterns (what time / days they usually write) and tries not to bother them when it's inconvenient.

## Character & Tone

**Name:** SpacedBro (working title, open for discussion)

**Character:**
- Bro-helper, not a teacher
- Short and concrete (no fluff)
- Friendly, supportive, no pathos
- Can use light slang / "okay", "got it", "let's go"
- Always offers actions via buttons when appropriate

Example tone:
> "Okay, added *ubiquitous*.  
> Want an example sentence right away?"  
> [Yes, example] [Later]

## High-level Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Telegram User                            │
└───────────────────────────┬─────────────────────────────────┘
                            │ messages / voice / photo / callbacks
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  Telegram Gateway                            │
│  (aiogram / python-telegram-bot)                             │
│  • receive text / voice / photo                              │
│  • inline buttons (yes/no, add, example...)                  │
│  • send proactive messages                                   │
└───────────┬───────────────────────────────┬─────────────────┘
            │                               │
            ▼                               ▼
┌───────────────────────┐       ┌──────────────────────────────┐
│  Media Processors     │       │   Intent & Extraction (LLM)  │
│  • STT (voice → text) │       │  • understand what to learn  │
│  • Vision / OCR       │       │  • suggest candidates        │
│                       │       │  • extract word/phrase       │
└───────────┬───────────┘       └──────────────┬───────────────┘
            │                                  │
            └────────────────┬─────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Core Services                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────┐  │
│  │ User Memory     │  │ Learning Items  │  │ SRS Engine  │  │
│  │ • tg_id         │  │ • card (front/  │  │ • next due  │  │
│  │ • activity log  │  │   back)         │  │ • ease      │  │
│  │ • preferred     │  │ • context       │  │ • interval  │  │
│  │   times         │  │ • state         │  │ • schedule  │  │
│  │ • language lvl  │  │                 │  │             │  │
│  └─────────────────┘  └─────────────────┘  └─────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │         Conversation / Generation (LLM)             │    │
│  │  • package review in an interesting format          │    │
│  │  • short replies in SpacedBro character             │    │
│  │  • generate examples, questions, mini-dialogues     │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              Scheduler / Proactive Layer                     │
│  • analyze user activity patterns                            │
│  • choose convenient window for review                       │
│  • message queue                                              │
└─────────────────────────────────────────────────────────────┘
```

## Tech Stack (preliminary)

- **Bot framework:** Python + aiogram 3.x (or python-telegram-bot)
- **LLM:** OpenAI (GPT-4o-mini / GPT-4.1-mini) — good balance of price/quality. Token will be provided to developers.
- **STT:** OpenAI Whisper / Telegram native / other
- **Vision:** GPT-4o vision or separate OCR
- **Storage:** PostgreSQL (or SQLite at the start) + Redis (queues, cache)
- **SRS:** classic SM-2 or simplified version (to be discussed)
- **Scheduler:** APScheduler / Celery / background tasks

## Planned Repository Structure

```
spaced-bro/
├── README.md                 ← you are here
├── openspec/                 ← OpenSpec specs & changes
│   ├── specs/                ← source of truth (system behavior)
│   │   ├── telegram-bot/
│   │   ├── srs-engine/
│   │   ├── user-memory/
│   │   ├── content-generation/
│   │   └── media/
│   ├── changes/              ← active changes
│   └── config.yaml
├── docs/                     ← additional notes, decisions
└── (later) src/              ← code
```

## How We Work

1. Discuss idea / feature here (in chat with Grok) or in issues.
2. Form a change in `openspec/changes/` using the OpenSpec approach.
3. Specification = requirements (SHALL) + scenarios (WHEN/THEN).
4. Engineers implement according to `tasks.md`.

All updates to the repository go through Pull Requests.

## Current Status

- [x] Repository created
- [x] High-level component diagram
- [ ] Agree on name and character
- [ ] Define MVP scope
- [ ] First OpenSpec change (core interaction + add word)

---

**Next step:** Agree on name/character and draft the first change (MVP: accept word → add to dictionary → simple review).
