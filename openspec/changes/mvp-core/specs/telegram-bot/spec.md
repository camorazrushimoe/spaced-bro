## ADDED Requirements

### Requirement: Bot identity and tone
The bot SHALL present as SpacedBro: short, concrete, friendly bro style; no teacher tone.

### Requirement: Default English UI with adaptation
Default UI English; adapt/mix per profile heuristics in design.

### Requirement: Start command
`/start` SHALL welcome the user, create profile if needed, **ask target language once**, and invite the first word or photo.

### Requirement: Entry points
Handle `/start`, text, photo, callbacks, on-demand review (`/review` and/or natural language).

### Requirement: Non-learning text
When text is not a learning request (greeting, thanks, off-topic chat),
the bot SHALL reply briefly and point the user to send a word/photo or start a review — and MUST NOT create learning candidates.

#### Scenario: Greeting
- **WHEN** the user sends "hi" / "thanks"
- **THEN** short ack without Add buttons for vocab candidates

### Requirement: Learning text and images
Extract up to 1–3 candidates with Add/Skip. Images via vision model; unusable image → short message.

### Requirement: Confirm back UI
Before save, show generated `back` with Save / Regenerate / Skip.

### Requirement: Duplicate and boost UI
Existing item → notify + Boost button.

### Requirement: Callback idempotency
Processing the same Add/Boost/Save callback id more than once SHALL not create duplicate cards or double-apply boost.

### Requirement: Review session
On-demand review SHALL state how many items are due, then present one card at a time until the user stops or none remain.

#### Scenario: Empty due
- **WHEN** no items are due
- **THEN** bot says so briefly

### Requirement: Inline buttons
Add, Skip, Save back, Regenerate back, Boost, language double-confirm, Show answer, quality ratings.

### Requirement: Proactive reviews
Within UTC windows and 1–3/UTC-day cap per design.

### Requirement: Friendly errors
LLM/API failures → short message, no stack trace, no partial save when add fails.

### Requirement: Voice
Voice messages MUST receive a short reply that voice is not supported yet; send text or photo.
