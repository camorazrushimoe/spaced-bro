## ADDED Requirements

### Requirement: Background scheduling
The system SHALL run a background process that periodically identifies users with due learning items.

### Requirement: UTC activity windows
The system SHALL estimate convenient send windows using activity timestamps in **UTC** (hour-of-day histogram or equivalent). If data is insufficient, a conservative default UTC window MAY be used.

#### Scenario: Respect activity
- **WHEN** selecting users for proactive messages
- **THEN** the system favors UTC hours matching the user's past activity

### Requirement: Low daily volume
The system SHALL send at most **1–3 proactive messages per user per day**, scaled by how active the user is (less active → fewer). It MUST NOT flood the user with many messages in a day.

#### Scenario: Daily cap
- **WHEN** a user has already reached their daily proactive limit
- **THEN** no further proactive review is sent until the next day

### Requirement: Back-off
The system MUST back off when the user has been inactive for a long period.

### Requirement: Non-spam behavior
Proactive messages are a convenience aid, not a requirement to deliver every due card immediately.
