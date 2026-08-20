## ADDED Requirements

### Requirement: User profile
The system SHALL maintain a profile per Telegram user (`telegram_id`) including creation time, last activity, target learning language, UI/communication language signals, and a rough level estimate.

#### Scenario: First interaction
- **WHEN** a user messages the bot for the first time
- **THEN** the system creates a profile with defaults: target language English, UI oriented to English

### Requirement: Single target language
The user SHALL learn only one target language at a time. The profile MUST store that language.

#### Scenario: Default target
- **WHEN** a new profile is created
- **THEN** target language is English unless the user selects another during onboarding

### Requirement: Change target language with double confirmation
Changing the target language SHALL require two explicit confirmations (propose change → user confirms → bot asks again → user confirms).

#### Scenario: Double confirm
- **WHEN** the user requests to change the language they are learning
- **THEN** the bot asks for confirmation twice before applying the change

### Requirement: UI language adaptation
The bot SHALL default to English UI. If the user writes in another language, the bot MAY adapt or mix languages. As the estimated level in the target language increases, the bot SHOULD prefer more (or fully) target-language communication. If the user struggles, the bot MAY mix languages.

#### Scenario: Strong learner
- **WHEN** the user has a large stable vocabulary (e.g. on the order of 100+ solid items) and strong review performance
- **THEN** the bot SHOULD communicate primarily in the target language

### Requirement: Level estimate
The system SHALL maintain a rough level estimate derived from signals such as dictionary size and review quality, and use it to adapt communication.

### Requirement: Activity tracking (UTC)
The system SHALL record activity timestamps in UTC (and preferably hour-of-day signals) for proactive scheduling.

#### Scenario: Update on message
- **WHEN** the user sends any message
- **THEN** last activity and UTC activity signals are updated

### Requirement: Privacy
The system SHALL store only the minimum data needed and MUST NOT share one user's data with other users.
