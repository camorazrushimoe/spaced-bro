## ADDED Requirements

### Requirement: Minimal user profile
The system SHALL maintain a minimal profile for each Telegram user identified by `telegram_id`. The profile MUST include at least creation time and last activity time.

#### Scenario: First interaction
- **WHEN** a user messages the bot for the first time
- **THEN** the system creates a user record with `telegram_id` and timestamps

### Requirement: Activity tracking
The system SHALL record or derive activity patterns (at minimum last active timestamp; preferably hour-of-day / day-of-week signals) so that convenient review windows can be estimated.

#### Scenario: Update on message
- **WHEN** the user sends any message to the bot
- **THEN** the system updates the user's last activity information

### Requirement: Language settings
The system SHALL store the user's source language and target learning language, with defaults suitable for the primary audience (source Russian, target English).

#### Scenario: Default languages
- **WHEN** a new user is created
- **THEN** source language defaults to Russian and target language defaults to English unless overridden

### Requirement: Privacy of memory
The system SHALL store only the minimum data needed for the learning experience and MUST NOT share one user's learning items or profile with other users.
