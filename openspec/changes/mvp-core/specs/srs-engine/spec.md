## ADDED Requirements

### Requirement: Simplified SM-2 algorithm
The system SHALL implement a simplified SM-2 spaced repetition algorithm for scheduling learning items.

#### Scenario: New item
- **WHEN** a new learning item is created
- **THEN** it is scheduled for an initial review within a short configurable interval (e.g. minutes to a few hours)

#### Scenario: Successful review
- **WHEN** the user rates a review as Good or Easy
- **THEN** the interval increases according to SM-2 rules (ease factor and repetitions updated)

#### Scenario: Failed review
- **WHEN** the user rates a review as Again (or equivalent)
- **THEN** the item returns to a short interval / learning state

### Requirement: Quality ratings
The system SHALL accept at least a minimal set of quality ratings (e.g. Again / Hard / Good / Easy) and map them to SM-2 updates. A simpler binary + quality scheme is acceptable if documented.

### Requirement: Deterministic next review
Given an item state and a quality rating, the SRS engine SHALL deterministically compute the new interval and next review timestamp.

### Requirement: Testability
The SRS calculations SHALL be unit-testable without Telegram or LLM dependencies.
