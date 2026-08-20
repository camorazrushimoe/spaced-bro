## ADDED Requirements

### Requirement: SpacedBro character
All LLM-generated user-facing text SHALL follow SpacedBro style: short, concrete, friendly bro tone, no teacher pathos, minimal fluff. Language of the reply SHALL follow UI adaptation rules (default English, adapt/mix by profile).

### Requirement: Intent extraction from text
The system SHALL extract learning intent and candidates (front + optional context) from user text via LLM structured output.

### Requirement: Intent extraction from images
The system SHALL extract candidates from images via a vision-capable model into the same structure as text extraction.

### Requirement: Cheap back (translation/definition)
When adding an item, the system SHALL call a short, low-token prompt to produce `back` (one-line translation or definition). It MUST prefer a cost-efficient model and MUST NOT use long multi-turn context for this step.

#### Scenario: Fill back on add
- **WHEN** a new item is confirmed
- **THEN** `back` is populated by the cheap prompt before or as the card is saved

### Requirement: Example generation
The system SHALL generate one short example sentence using the target word/phrase when requested.

### Requirement: Review packaging
Reviews SHALL be packaged briefly (front, optional example, clear buttons). Richer formats are optional later.

### Requirement: Brevity
Replies SHOULD stay within a few short sentences; actions via buttons preferred over long text.
