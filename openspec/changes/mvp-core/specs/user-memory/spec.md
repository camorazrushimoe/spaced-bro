## ADDED Requirements

### Requirement: User profile
The system SHALL store per Telegram user at least: `native_lang`, `target_lang`, UI/detected language signals, `level_estimate`, UTC activity fields, proactive daily counters.

#### Scenario: First interaction
- **WHEN** a user first messages the bot
- **THEN** a profile is created with `native_lang` default `ru`, `target_lang` default `en`

### Requirement: Native language
`native_lang` SHALL be the language used for card `back` (meanings/translations). Default `ru` for MVP primary audience.

### Requirement: Single target language
Only one `target_lang` at a time. Change requires double confirmation (propose → confirm → confirm again).

#### Scenario: Double confirm applies
- **WHEN** the user completes both confirmations
- **THEN** `target_lang` updates

#### Scenario: Single confirm insufficient
- **WHEN** the user confirms only once
- **THEN** `target_lang` is unchanged

### Requirement: Onboarding question
On `/start` for a new user, the bot SHALL ask once which language they want to learn (default English if skipped).

### Requirement: Activity UTC
Activity timestamps and histograms SHALL use UTC.

### Requirement: Privacy
Store only minimum data; no cross-user sharing.
