## ADDED Requirements

### Requirement: Bot identity and tone
The bot SHALL present itself as SpacedBro and communicate in a short, concrete, friendly "bro" style. It MUST avoid teacher-like, verbose, or formal language.

### Requirement: Default English UI with adaptation
The bot SHALL default to English. It SHALL adapt or mix languages based on the user profile (input language and level estimate) per user-memory rules.

#### Scenario: Short confirmation
- **WHEN** the user successfully adds a word
- **THEN** the bot replies with a brief confirmation and optional action buttons

### Requirement: Start command
The bot SHALL respond to `/start` with a short welcome explaining the idea (text or images to learn; spaced reviews) and MAY ask what language the user wants to learn (default English).

#### Scenario: First start
- **WHEN** a user sends `/start` for the first time
- **THEN** the bot creates a profile if needed and sends a short welcome + call to action

### Requirement: Entry points
The bot SHALL handle at least: `/start`, plain text, photos, callback queries, and an on-demand review path (natural language and/or `/review`).

### Requirement: Text message handling
The bot SHALL accept text containing words, phrases, or short texts for learning.

#### Scenario: Explicit single word
- **WHEN** the user clearly requests to learn a word/phrase
- **THEN** the bot extracts it and offers to add it (with buttons if needed)

#### Scenario: Multi-item text
- **WHEN** the user sends longer text
- **THEN** the bot may suggest up to 1–3 candidates with Add/Skip buttons

### Requirement: Image message handling
The bot SHALL accept photos and extract candidates via a vision-capable model.

#### Scenario: Useful photo
- **WHEN** the image contains learnable text
- **THEN** the bot proposes up to 1–3 candidates with buttons

#### Scenario: Unusable image
- **WHEN** nothing useful is found
- **THEN** the bot replies briefly and invites another try

### Requirement: Duplicate and boost UI
When a candidate already exists, the bot SHALL say so and offer a Boost button (reset to frequent reviews).

### Requirement: Inline buttons
The bot SHALL use inline buttons for Add, Skip, Boost, language double-confirm, Show answer, example, and review quality.

### Requirement: Review interaction
The bot SHALL support interactive review (front → reveal back → quality rating → SRS update).

#### Scenario: On-demand review
- **WHEN** the user requests a review
- **THEN** the bot presents a due item with rating/reveal buttons

### Requirement: Proactive reviews
The bot SHALL be able to send proactive reviews within UTC activity windows subject to the 1–3 per day cap.

### Requirement: Friendly errors
On LLM/API failures or unexpected errors, the bot SHALL reply with a short, clear message (no technical details) and invite retry.

### Requirement: Voice out of scope
Voice MUST NOT be processed in MVP; the bot MAY say voice is not supported yet.
