## ADDED Requirements

### Requirement: Personal learning dictionary
The system SHALL maintain a personal set of learning items (cards) for each user. Each item MUST have a front (word or phrase to learn) and a back (translation, meaning, or short definition).

#### Scenario: Add item
- **WHEN** the user confirms addition of a candidate
- **THEN** the system creates a learning item linked to that user with initial SRS state (new)

### Requirement: Optional context
The system SHALL allow storing optional context (original sentence or note) with a learning item.

### Requirement: Language pair
Each learning item SHALL record the language pair it belongs to (default en-ru for MVP).

### Requirement: Query due items
The system SHALL be able to retrieve learning items that are due for review for a given user at a given time.

#### Scenario: Due query
- **WHEN** the scheduler or review flow requests due items for a user
- **THEN** only items whose `next_review_at` is in the past (or null for new) are returned, ordered appropriately

### Requirement: Update after review
After a review rating is received, the system SHALL update the item's SRS fields (`ease`, `interval`, `repetitions`, `next_review_at`, `last_review_at`, status) according to the SRS engine rules.
