## ADDED Requirements

### Requirement: Background scheduling
The system SHALL run a background process that periodically identifies users with due learning items.

### Requirement: Convenient windows
The system SHALL prefer sending proactive reviews during times when the user has historically been active (derived from activity data). If insufficient data exists, a conservative default window MAY be used.

#### Scenario: Respect activity
- **WHEN** selecting users for proactive messages
- **THEN** the system favors hours/days matching the user's past activity patterns

### Requirement: Rate limiting
The system SHALL enforce a configurable maximum number of proactive review messages per user per day and MUST back off when the user has been inactive for a long period.

#### Scenario: Daily limit
- **WHEN** a user has already received the maximum proactive messages today
- **THEN** no further proactive review is sent until the next day (or limit reset)

### Requirement: Non-spam behavior
The system MUST NOT flood the user. Proactive messages are a convenience, not a requirement for every due card at every moment.
