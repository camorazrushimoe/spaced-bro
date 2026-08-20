## ADDED Requirements

### Requirement: Simplified SM-2 algorithm
The system SHALL implement a simplified SM-2 spaced repetition algorithm with documented default intervals and quality mapping.

Suggested defaults (tunable in config):
- New item → first review in ~10–30 minutes (or next convenient window)
- Qualities: Again / Hard / Good / Easy
- Again → short interval (minutes–hours)
- Good/Easy → growing intervals (e.g. days → weeks → ~90 days and beyond)
- Optional max interval cap (e.g. 180 days)

#### Scenario: New item
- **WHEN** a new learning item is created
- **THEN** it is scheduled for an initial short-interval review

#### Scenario: Successful review
- **WHEN** the user rates Good or Easy
- **THEN** interval and ease increase per SM-2 rules

#### Scenario: Failed review
- **WHEN** the user rates Again
- **THEN** the item returns to a short interval / learning state

### Requirement: Boost / reset schedule
The system SHALL support resetting an item's SRS state to a frequent "new/learning" schedule (boost) while preserving card content.

#### Scenario: Boost applied
- **WHEN** boost is confirmed for an item
- **THEN** next reviews are scheduled as for a newly added item

### Requirement: Quality ratings
The system SHALL accept at least Again / Hard / Good / Easy (or a documented simpler mapping).

### Requirement: Deterministic next review
Given state + quality (or boost), the engine SHALL deterministically compute the next interval and timestamp.

### Requirement: Testability
SRS calculations SHALL be unit-testable without Telegram or LLM.
