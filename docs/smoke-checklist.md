# SpacedBro — End-to-End Smoke Checklist

BON-34 (tasks.md §7): the five required smoke scenarios, each with the exact
expected behaviour and a concrete verification (chat observation and/or a
log/metrics grep). Run after every `compose up` or local boot, per backend.

**Pre-flight (Definition of Done: "secrets env-only"):**

- [ ] `docker compose config | grep -E 'BOT_TOKEN|OPENAI_API_KEY'` shows the
      values resolved from the local gitignored `.env` — never from a
      committed file.
- [ ] `grep -rE "BOT_TOKEN=sk-|OPENAI_API_KEY=sk-" src/ compose.yml Dockerfile
      alembic/ .env.example` returns **nothing** (no secrets in the repo).
- [ ] Pre-deploy key presence: `BOT_TOKEN` is set for every environment;
      `OPENAI_API_KEY` is set whenever the resolved LLM provider is `openai`
      (the `preprod`/`production` default). A missing key aborts startup with
      exit code 78 and an `ERROR` log naming the variable — verify that by
      deleting the key locally and confirming the container exits 78, then
      restore it.
- [ ] After boot, `curl http://<host>:8080/healthz` → `{"status":"ok",
      "database":"ok", ...}` and `docker compose ps` shows `(healthy)`.

> **Container note (dev-env sandbox):** host-side port publishing may be
> unavailable (no docker-proxy). Reach the port via the container's bridge
> IP: `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}
> {{end}}' <container>` then `curl http://<bridge-ip>:8080/healthz`.

---

## 1. Happy path — text add with confirm-back

Send a learning word to the bot (e.g. `serendipity`).

- [ ] The bot replies `Here's what I found — tap **Add** on the ones you want:`
      with an **[Add] [Skip]** button for the candidate (design §3).
- [ ] Tapping **Add** shows the **confirm-back card**: the word in bold, an
      optional context line, and a short generated meaning in the user's
      native language — with **[Save] [Wrong — regenerate] [Skip]** buttons
      (design §5 step 3–4). *Nothing is persisted yet.*
- [ ] Tapping **Save** → `Saved ✅ First review in ~20 min.`
- [ ] **Verification (DB):** a new `learning_items` row exists with the
      New SRS state (`ease=2.5`, `interval_minutes=20`, `repetitions=0`,
      `status='learning'`, `next_review_at ≈ now+20min`).
- [ ] **Verification (metrics):** `curl -s http://<host>:8080/metrics |
      grep spacedbro_llm_calls_total` shows `kind="text",outcome="ok"`
      counters incremented (extraction + back generation) and
      `spacedbro_items_total` incremented after the next scrape.

*Image variant (needs a vision-capable model per backend): send a photo of a
word → same candidate → confirm-back → Save flow; `kind="vision"` counters
increment. Without vision support the bot replies
`Photos aren't supported in this setup yet 😨 Send me text instead.` — that
reply is the PASS for this item on non-vision backends.*

## 2. Non-learning text

Send chit-chat that is not a learning request (e.g. `hey how are you`).

- [ ] The bot replies with the short ack
      `Got it 👍 Send me a word or a photo to learn it — or send /review
      for your due cards.`
- [ ] **No** candidate list, **no** [Add] buttons, **no** item created.
- [ ] **Verification (DB):** `learning_items` count unchanged.

## 3. Duplicate + boost

Send a word already saved in scenario 1 (any casing/whitespace variant, e.g.
`Serendipity `).

- [ ] Extraction offers the candidate again (dedup happens at Add time).
- [ ] Tapping **Add** → the bot replies
      `You already know "serendipity" 🧠 Want to boost it?` with
      **[Boost] [Skip]** buttons — **no second row** is created (design §5).
- [ ] Tapping **Boost** → `Boosted ⚡ — back to the start of the queue.`
- [ ] **Verification (DB):** still exactly ONE row for that
      `normalized_front`; its SRS state is reset to New
      (`ease=2.5`, `interval_minutes=20`, `repetitions=0`,
      `next_review_at ≈ now+20min`, `last_review_at=NULL`).
- [ ] **Verification (metrics):** `spacedbro_items_total` unchanged.

## 4. Review session (on-demand)

Make at least one card due (wait ~20 min, or nudge `next_review_at` into the
past for a smoke run), then send `/review` (or NL: `review my words`).

- [ ] The bot reports **how many are due**:
      `You've got <b>N</b> card(s) due — let's go 🚀` (design §7).
- [ ] It presents **one** card: the front, then a **[Show answer]** button.
- [ ] After **Show answer** → the back is revealed with **[Again] [Hard]
      [Good] [Easy]** + **[Stop]** buttons.
- [ ] Rating a card advances its SRS state (e.g. **Good** from New →
      `ease=2.5`, `interval_minutes=60`, `repetitions=1`, status stays
      `learning` per the BON-28 engine) and the bot offers the **next due
      card** or finishes with `That's all for now — nothing due. Nice work 🎉`.
- [ ] **[Stop]** mid-session → `Stopped ⏸️ Your due cards will wait — send
      /review any time.` — unattended cards **stay due, no penalty**.
- [ ] **Verification (DB):** reviewed item has `last_review_at` set and
      `next_review_at` in the future per the engine mapping.
- [ ] **Verification (scheduler):** on-demand reviews do **not** increment
      `users.proactive_count` (design §8).

## 5. Proactive dry-run

Boot (or restart) the container with `SCHEDULER_DRY_RUN=1` (a due card must
exist and the current time must be inside the user's allowed window — e.g.
09:00–21:00 UTC on cold start).

- [ ] Within one pass interval (`SCHEDULER_INTERVAL_MINUTES`, default 5):
      container logs contain
      `proactive DRY-RUN: would nudge <telegram_id> (N due)` and
      `proactive pass done: checked=… sent=0 skipped=… dry_run=True`.
- [ ] **No Telegram message is sent** and `users.proactive_count` is
      **unchanged** — dry-run only observes (BON-33; this is the spec's
      "proactive dry-run" smoke).
- [ ] **Verification (health):** `GET /healthz` still reports
      `{"status":"ok"}` during and after the pass.

---

## Error-path spot checks (design §9 — no stack traces, ever)

While any of the above scenarios is running, confirm the user-visible
behaviour for each injected failure (or the first real occurrence):

- [ ] LLM/API failure during `back` generation → short
      `Couldn't generate the meaning. Want to try again?` + **[Try again]
      [Skip]**; **nothing is saved** (no partial card, DB count unchanged).
- [ ] Bad JSON from the LLM → short retry message, no partial card.
- [ ] Unreadable image → short `Couldn't process that photo. Try another one?`.
- [ ] Voice message → **must** get `Voice isn't supported yet 😨 Send me
      text or a photo instead.` (never silence).
- [ ] User messages NEVER contain a stack trace, an exception class name, or
      internal paths — unhandled failures get the global short reply
      (`Something glitched on my end 🐻 Try again in a sec?`) while the full
      traceback is logged server-side at ERROR level.

## Metrics sanity (final)

- [ ] `curl -s http://<host>:8080/metrics` returns Prometheus text
      (`text/plain; version=0.0.4`) with `spacedbro_users_total`,
      `spacedbro_items_total`, `spacedbro_items_due_total`,
      `spacedbro_llm_calls_total{...}`, `spacedbro_llm_errors_total{...}`
      (counter lines absent until first observed) — stable, sorted, and
      comparable across scrapes.
- [ ] `spacedbro_items_due_total` matches what `/review` reports as due at
      the same moment.
