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

**Name:** SpacedBro

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

## Tech Stack (MVP)

- **Bot framework:** Python + aiogram 3.x
- **LLM:** OpenAI (GPT-4o-mini / similar cost-efficient model). Token will be provided to developers.
- **Storage:** PostgreSQL + Redis (recommended)
- **SRS:** Simplified SM-2
- **Scheduler:** Background jobs (APScheduler / Celery / equivalent)

Voice and image processing are planned for Phase 2.

## Planned Repository Structure

```
spaced-bro/
├── README.md
├── openspec/
│   ├── specs/                  # source of truth (after archive)
│   ├── changes/
│   │   └── mvp-core/           # current active change
│   │       ├── proposal.md
│   │       ├── design.md
│   │       ├── tasks.md
│   │       └── specs/
│   └── config.yaml
├── docs/
└── (later) src/
```

## How We Work

1. Discuss idea / feature in chat or issues.
2. Form a change in `openspec/changes/` using the OpenSpec approach.
3. Specification = requirements (SHALL) + scenarios (WHEN/THEN).
4. Engineers implement according to `tasks.md`.
5. All updates go through Pull Requests.

## Current Status

- [x] Repository created
- [x] High-level component diagram
- [x] Name and character locked (SpacedBro)
- [x] MVP scope defined
- [x] First OpenSpec change: `openspec/changes/mvp-core/`
  - proposal, design, tasks, and requirements for core domains

**Hand-off ready:** The `mvp-core` change is the specification package for engineers to estimate and implement.

---

See `openspec/changes/mvp-core/` for the full MVP specification.
