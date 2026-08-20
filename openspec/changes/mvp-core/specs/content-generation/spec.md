## ADDED Requirements

### Requirement: SpacedBro character
Generated user-facing text SHALL match SpacedBro tone and the active UI language policy.

### Requirement: Intent extraction from text
The system SHALL distinguish learning intent vs non-learning chat.

#### Scenario: Learning content
- **WHEN** the user sends a word, phrase, or text with extractable vocab to learn
- **THEN** structured candidates are returned

#### Scenario: Non-learning content
- **WHEN** the user sends a greeting or off-topic message with no learnable request
- **THEN** extraction returns no candidates (bot uses ack path)

### Requirement: Intent extraction from images
Vision model returns the same candidate structure, or an empty set if nothing useful.

### Requirement: Back in native_lang
The cheap `back` prompt SHALL target the user's `native_lang` and request a single short line only.

### Requirement: Example generation
One short example sentence on request.

### Requirement: Brevity
Few short sentences; prefer buttons for actions.
