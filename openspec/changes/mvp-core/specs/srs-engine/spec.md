## ADDED Requirements

### Requirement: Deterministic simplified SM-2
The system SHALL implement the exact mapping defined in `design.md` §6 (new state, Again/Hard/Good/Easy, max interval 180 days, boost = new state). Given `(state, quality, now_utc)`, the next state SHALL be fully determined with no optional branches.

#### Scenario: New item
- **WHEN** a new item is created at time T
- **THEN** `interval_minutes = 20`, `ease = 2.5`, `repetitions = 0`, `next_review_at = T + 20 minutes`

#### Scenario: Again
- **WHEN** the user rates Again
- **THEN** `repetitions = 0`, `interval_minutes = 10`, ease decreases by 0.2 but not below 1.3, `next_review_at = now + 10 minutes`

#### Scenario: Hard
- **WHEN** the user rates Hard
- **THEN** interval becomes `max(10, int(interval_minutes * 1.2))`, ease decreases by 0.15 but not below 1.3, repetitions increment by 1

#### Scenario: Good first success
- **WHEN** repetitions is 0 and user rates Good
- **THEN** `interval_minutes = 1440`, repetitions become 1

#### Scenario: Easy increases ease
- **WHEN** the user rates Easy
- **THEN** interval grows faster than Good (per design formula) and ease increases by 0.15

#### Scenario: Cap
- **WHEN** computed interval would exceed 259200 minutes
- **THEN** interval is set to 259200

### Requirement: Boost
Boost SHALL set SRS fields equal to the new-item state while preserving front/back/context.

#### Scenario: Boost from long interval
- **WHEN** an item with a multi-day interval is boosted at time T
- **THEN** it matches new-item state at T

### Requirement: Injectable clock
SRS and scheduling logic SHALL accept an injectable UTC clock so tests can freeze time. Production uses real UTC.

### Requirement: Testability
SRS calculations SHALL be unit-testable without Telegram, LLM, or a real wall clock.
