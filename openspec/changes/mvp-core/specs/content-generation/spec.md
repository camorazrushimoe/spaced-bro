## ADDED Requirements

### Requirement: SpacedBro character in all generated text
All LLM-generated user-facing text SHALL follow the SpacedBro character: short, concrete, friendly bro style, no teacher tone, minimal fluff.

### Requirement: Intent extraction from text
The system SHALL use an LLM to extract learning intent and candidate items (word or short phrase + optional translation/context) from user text.

#### Scenario: Explicit request
- **WHEN** the user clearly indicates a word or phrase to learn
- **THEN** the extractor returns that item with high confidence

#### Scenario: Suggestion from text
- **WHEN** the user sends a longer text without explicit instruction
- **THEN** the extractor may propose a small number of useful candidates for confirmation

### Requirement: Intent extraction from images
The system SHALL use a vision-capable model to extract candidate learning items from user-uploaded images (screenshots, photos of text, etc.).

#### Scenario: Image with useful text
- **WHEN** the user sends an image containing English words or phrases suitable for learning
- **THEN** the extractor returns a small set of candidates (word/phrase + optional context) in the same structure as text extraction

#### Scenario: No useful text
- **WHEN** the image contains no extractable learning candidates
- **THEN** the system signals that nothing useful was found so the bot can reply briefly

### Requirement: Example generation
The system SHALL be able to generate a short example sentence that uses the target word/phrase naturally.

#### Scenario: Request example
- **WHEN** the user (or flow) requests an example for an item
- **THEN** the bot returns one concise example sentence in SpacedBro style

### Requirement: Review packaging
The system SHALL package reviews in a short, interesting way (at minimum: present the front, optionally an example, and clear action buttons). Richer formats (movie-style, mini-dialogue) are optional for later phases.

### Requirement: Brevity
Generated replies SHOULD stay within a small number of short sentences. The system MUST prefer buttons over long explanatory text for actions.
