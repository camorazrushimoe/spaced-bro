## ADDED Requirements

### Requirement: Bot identity and tone
The bot SHALL present itself as SpacedBro and communicate in a short, concrete, friendly "bro" style. It MUST avoid teacher-like, verbose, or formal language.

#### Scenario: Short confirmation
- **WHEN** the user successfully adds a word
- **THEN** the bot replies with a brief confirmation (e.g. "Okay, added *ubiquitous*.") and optional action buttons

### Requirement: Start command
The bot SHALL respond to `/start` with a short welcome that explains the core idea (send words/phrases or images to learn, reviews via spaced repetition) and invites the user to send the first item.

#### Scenario: First start
- **WHEN** a user sends `/start` for the first time
- **THEN** the bot creates a user record if needed and sends a short welcome + call to action

### Requirement: Text message handling
The bot SHALL accept plain text messages containing words, phrases, or short texts intended for learning.

#### Scenario: Explicit single word
- **WHEN** the user sends a clear request to learn a specific word or phrase
- **THEN** the bot extracts the item and offers to add it (with confirmation buttons if needed)

#### Scenario: Ambiguous or multi-item text
- **WHEN** the user sends a sentence or text with multiple potential items
- **THEN** the bot suggests up to a small number of candidates (e.g. 1–3) with buttons to add or skip each

### Requirement: Image message handling
The bot SHALL accept photo messages (screenshots, photos of words/phrases, book pages, UI text, etc.) and extract candidate learning items from them via a vision-capable model.

#### Scenario: Photo with readable English text
- **WHEN** the user sends an image containing English words or phrases
- **THEN** the bot proposes a small number of candidates (e.g. 1–3) with buttons to add or skip each

#### Scenario: Unusable image
- **WHEN** the image has no useful text for learning
- **THEN** the bot replies briefly that it could not find anything to learn and invites another try

### Requirement: Inline buttons for actions
The bot SHALL use inline keyboard buttons for clear actions such as Add, Skip, Confirm, Show answer, rate review quality, and request an example.

#### Scenario: Add confirmation
- **WHEN** the bot proposes candidates (from text or image)
- **THEN** each candidate is accompanied by actionable buttons (e.g. [Add] [Skip])

### Requirement: Review interaction
The bot SHALL support interactive review of due learning items, including showing the front, revealing the back on request, and collecting a quality rating that updates the SRS state.

#### Scenario: On-demand review
- **WHEN** the user requests a review (via command, button, or natural language)
- **THEN** the bot presents a due item in SpacedBro style and provides rating / reveal buttons

#### Scenario: After rating
- **WHEN** the user rates a review
- **THEN** the bot updates the item's SRS state and may offer the next due item or a short closing message

### Requirement: Proactive review messages
The bot SHALL be able to initiate review messages to the user when items are due and the current time falls within a convenient activity window for that user, subject to daily rate limits.

#### Scenario: Proactive delivery
- **WHEN** a user has due items and is in a convenient window and under the daily proactive limit
- **THEN** the bot may send a short review message without the user initiating it

### Requirement: Voice out of scope for MVP
The bot MUST NOT process voice messages in MVP. If a voice message is received, it MAY reply briefly that voice is not supported yet.
