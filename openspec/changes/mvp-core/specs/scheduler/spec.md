## ADDED Requirements

### Requirement: In-process scheduler
The system SHALL run proactive scheduling in-process (APScheduler) in the single bot process for MVP.

### Requirement: UTC day boundary
The proactive daily cap resets at **UTC midnight**. A "day" is a UTC calendar date.

### Requirement: Cap 1–3 and exclusions
Each user receives at most 1–3 proactive messages per UTC day per the activity scaling in design. **On-demand reviews do not count** toward this cap.

#### Scenario: Cap reached
- **WHEN** proactive_count for today's UTC date is already at the user's limit
- **THEN** no further proactive message is sent until the next UTC date

### Requirement: Cold-start window
If the user has no activity histogram, proactive sends are allowed only between **09:00 and 21:00 UTC**.

### Requirement: Active window (warm start)
If the user HAS a non-empty activity histogram, proactive sends are allowed only during the user's **peak active UTC hour ±1 hour** (wrapping at midnight; ties resolve to the earlier hour). The histogram is the user's recorded activity pattern (design §8: the bot tries not to bother them when it is inconvenient).

### Requirement: Back-off
If `last_active_at` is older than **14 days**, the system SHALL skip proactive sends for that user.

### Requirement: Unattended due items
Due items not sent proactively remain due for on-demand review; no automatic discard.

### Requirement: Single-instance
MVP assumes one bot instance; no distributed lock is required. This SHALL be documented for operators.
