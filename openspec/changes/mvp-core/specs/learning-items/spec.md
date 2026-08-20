## ADDED Requirements

### Requirement: Personal learning dictionary
The system SHALL maintain learning items per user with `front`, `back`, optional `context`, and SRS fields including `interval_minutes` and UTC `next_review_at`.

### Requirement: Front normalization
Uniqueness SHALL use `normalized_front = " ".join(front.casefold().split())`. Two fronts that normalize equal are the same item for that user.

#### Scenario: Case and spaces
- **WHEN** the user adds `Hello` and later ` hello `
- **THEN** the second is treated as a duplicate of the first

### Requirement: Back in native language
`back` SHALL be generated as a short translation or definition **in the user's `native_lang`** via a cheap one-line LLM prompt.

### Requirement: Confirm back before save
The system SHALL show `front` and generated `back` and require explicit Save before persisting. The user MUST be able to reject and regenerate `back` or skip without saving.

#### Scenario: Save
- **WHEN** the user taps Save after seeing `back`
- **THEN** the card is stored with new SRS state

#### Scenario: Reject back
- **WHEN** the user indicates `back` is wrong
- **THEN** the system regenerates `back` (or offers skip) and does not save until Save

#### Scenario: Back LLM failure
- **WHEN** `back` generation fails
- **THEN** no card is saved; user sees a short error and can retry

### Requirement: Duplicates and boost
#### Scenario: Duplicate
- **WHEN** normalized front already exists
- **THEN** bot informs the user and offers Boost without creating a second row

### Requirement: Due query
Items with `next_review_at <= now_utc` are due, ordered by `next_review_at` ascending.
