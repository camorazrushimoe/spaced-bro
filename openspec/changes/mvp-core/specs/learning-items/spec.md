## ADDED Requirements

### Requirement: Personal learning dictionary
The system SHALL maintain a personal set of learning items for each user. Each item MUST have a `front` (word/phrase) and a `back` (translation, meaning, or short definition).

#### Scenario: Add item
- **WHEN** the user confirms addition of a candidate
- **THEN** the system creates a learning item with initial SRS state and a `back` produced by a cheap LLM prompt

### Requirement: Fill back via LLM
When adding an item, the system SHALL obtain `back` using a short, low-token LLM prompt (definition/translation), not leave it empty.

### Requirement: Optional context
The system SHALL allow storing optional context (sentence, note, image hint).

### Requirement: Unique front per user
For a given user, the system SHALL treat normalized `front` as unique.

#### Scenario: Duplicate detected
- **WHEN** the user tries to add an item whose front already exists in their dictionary
- **THEN** the bot informs them it is already saved and offers to **boost** learning (reset SRS to a frequent schedule)

### Requirement: Boost learning
Boosting an existing item SHALL reset its SRS schedule to a short/frequent interval (as if newly added) without deleting the card or its `back`/context.

#### Scenario: Forgotten long-interval card
- **WHEN** the user boosts an item that was on a long interval (e.g. ~90 days) because they no longer remember it
- **THEN** the item returns to frequent review scheduling

### Requirement: Query due items
The system SHALL retrieve items due for review for a user at a given time.

### Requirement: Update after review
After a quality rating, the system SHALL update SRS fields according to the SRS engine rules.
