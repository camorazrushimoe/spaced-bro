# SpacedBro

> Telegram bot companion for language learning with spaced repetition (SRS).
> Short, concrete, in the style of "bro, let's learn this word".

**Repository for specification and development.**  
We use [OpenSpec](https://github.com/Fission-AI/OpenSpec) — lightweight spec-driven development.

## Product Idea

The user sends the bot:
- a word / phrase
- a piece of text
- an image (screenshot, photo of a word)
- a voice message (Phase 2)

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
                            │ messages / photo / callbacks
                            │ (voice → Phase 2)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  Telegram Gateway                            │
│  (aiogram)                                                   │
│  • receive text / photo                                      │
│  • inline buttons (yes/no, add, example...)                  │
│  • send proactive messages                                   │
└───────────┬───────────────────────────────┬─────────────────┘
            │                               │
            ▼                               ▼
┌───────────────────────┐       ┌──────────────────────────────┐
│  Media (Images)       │       │   Intent & Extraction (LLM)  │
│  • Vision / multimodal│       │  • understand what to learn  │
│                       │       │  • suggest candidates        │
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
- **LLM:** environment-aware router (`spacedbro/llm`) — local OpenAI-compatible server (Gemma 4) for development, OpenAI `gpt-5.6-luna` for preprod/production; one client, switched by env vars only (see `openspec/changes/llm-router/`)
- **Storage:** SQLite (single file on a Docker Compose named volume). PostgreSQL/Redis not required for MVP.
- **SRS:** Simplified SM-2
- **Scheduler:** In-process APScheduler (single instance, same process as the bot)
- **Images:** Multimodal / vision model (in MVP)
- **Voice:** Deferred to Phase 2

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
- [x] MVP scope defined (text + images; voice later)
- [x] First OpenSpec change: `openspec/changes/mvp-core/`
  - proposal, design, tasks, and requirements for core domains

**Hand-off ready:** The `mvp-core` change is the specification package for engineers to estimate and implement.

---

See `openspec/changes/mvp-core/` for the full MVP specification.

## Development & running

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (or pip + venv)
- Docker + Docker Compose (for the containerized deploy)

### Configuration

Configuration is environment-only — no secrets in code or committed files.
Copy `.env.example` to `.env` and fill in real values:

```bash
cp .env.example .env
# edit .env: BOT_TOKEN, APP_ENV, (OPENAI_API_KEY for preprod/production)
```

- **Required:** `BOT_TOKEN`, `APP_ENV` (exactly `development` | `preprod` | `production`).
- **Provider-driven key check:** `OPENAI_API_KEY` is required only when the
  resolved LLM provider is `openai` (the `preprod`/`production` default). A
  fully local setup never needs it — the development backend uses a fixed
  `local-dev` sentinel key that is never read from `OPENAI_API_KEY`.
- Any configuration error (unknown `APP_ENV`, invalid `LLM_PROVIDER`, missing
  key, bad `LLM_TIMEOUT_SECONDS`/`LLM_MAX_RETRIES`) aborts startup with exit
  code **78** (`EX_CONFIG`) and an `ERROR` log naming the offending variable —
  before the service port is bound.

**LLM resolution** (pinned defaults; each variable may be overridden
independently — `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `OPENAI_API_KEY`,
`LLM_TIMEOUT_SECONDS` (default 30), `LLM_MAX_RETRIES` (default 2)):

| `APP_ENV` | provider | base_url | model | api_key |
|---|---|---|---|---|
| `development` | `openai_compatible` | `http://localhost:11434/v1` | `gemma-4-e2b-it` | `local-dev` (sentinel) |
| `preprod` | `openai` | `https://api.openai.com/v1` | `gpt-5.6-luna` | `OPENAI_API_KEY` (required) |
| `production` | `openai` | `https://api.openai.com/v1` | `gpt-5.6-luna` | `OPENAI_API_KEY` (required) |

Optional: `DATABASE_URL` (defaults to `sqlite:////data/spacedbro.db`),
`HEALTH_HOST`, `HEALTH_PORT`, `LOG_LEVEL`, and the proactive scheduler
`SCHEDULER_INTERVAL_MINUTES` (default 5 — "job every N minutes", design §8)
and `SCHEDULER_DRY_RUN` (default off — the pass runs and logs, nothing is
sent; the smoke checklist's "proactive dry-run").

### Local LLM: Gemma 4 via Ollama

Development defaults point at an OpenAI-compatible local server
(`http://localhost:11434/v1`) running a Gemma 4 variant:

```bash
ollama pull gemma-4-e2b-it    # E2B (fast) or a larger variant for quality
```

A different local server or tag (LM Studio, vLLM, another variant) is
selected by overriding `LLM_BASE_URL` / `LLM_MODEL` / `LLM_PROVIDER` — no
extra config variable exists. Vision (image add) needs a vision-capable
local model; without it the bot answers "image add is not available in this
environment" (the `vision_not_supported` domain error) instead of crashing.

### Run locally

```bash
uv venv .venv
uv pip install -e ".[dev]"
set -a; . ./.env; set +a          # export secrets for the process
uv run python -m spacedbro
```

The process resolves its configuration first (failing fast with exit 78 on
any config error), applies Alembic migrations, builds the LLM client (the
only door to the LLM — handlers receive it by injection and never
instantiate provider clients themselves), starts the in-process APScheduler
and the health server, then begins Telegram long polling.

**Smoke checklist** (per backend): text add → extraction returns candidates;
photo add → vision extraction works (local: requires a vision-capable model);
review cycle → `back` generation produces the short reply; proactive
dry-run → with `SCHEDULER_DRY_RUN=1` and a due card, the log shows
`proactive DRY-RUN: would nudge …` within one pass interval (no message is
sent, nothing is counted). The INFO log line
`LLM resolved configuration: APP_ENV=… provider=… model=… base_url=…`
confirms which backend is active.

### Tests

```bash
uv run python -m pytest
```

### Run with Docker Compose

```bash
docker compose up --build
```

This builds the image, applies migrations on startup, mounts the SQLite file at
`/data/spacedbro.db` on a named volume (`spacedbro-data`), and exposes a
HEALTHCHECK-backed `/healthz` on port 8080. Verify:

```bash
curl http://localhost:8080/healthz   # {"status":"ok","database":"ok",...}
docker compose ps                    # STATUS shows (healthy)
```

### Proactive scheduling (operator note)

The bot runs an **in-process APScheduler** (same process, same event loop)
that, every `SCHEDULER_INTERVAL_MINUTES` (default 5), nudges users who have
cards due — one nudge per user per pass, reporting how many cards are due
and pointing to `/review`. Per-user rules (design §8, `scheduler` spec):

- **Daily cap 1–3 per UTC day** (day = UTC calendar date, reset at UTC
  midnight), scaled by activity: 0 messages in the last 7 days → 1;
  ≥3 distinct UTC hours active → 3; else 2.
- **On-demand reviews never count** toward the cap — only proactive sends do.
- **Cold start** (no activity histogram): proactive only 09:00–21:00 UTC.
- **Back-off**: no activity for 14 days → proactive skipped for that user.
- Proactive and on-demand **share the same due queue**: a nudge does not
  consume it — unattended cards stay due for `/review`, no penalty.

**⚠️ Single-instance assumption — no distributed lock in MVP.** The
scheduler lives inside the bot process and coordinates nothing across
processes: running two `bot` replicas would **double proactive sends and
corrupt the daily counters**. Operate exactly **one** replica
(`restart: unless-stopped` is fine; scaling out is not). The documented
upgrade path is Postgres/Redis + a distributed lock (design §10) — out of
MVP scope.

