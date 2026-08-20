## ADDED Requirements

### Requirement: Accept user photos
The system SHALL accept photo messages from Telegram users as a valid input channel for learning items in MVP.

### Requirement: Vision-based extraction
The system SHALL process uploaded images with a vision-capable model (or equivalent OCR + LLM pipeline) to extract candidate words and short phrases suitable for learning.

#### Scenario: Successful extraction
- **WHEN** an image contains readable English text relevant to vocabulary learning
- **THEN** the system produces structured candidates compatible with the text extraction flow

### Requirement: No permanent image storage (default)
The system SHOULD process images and discard them after extraction unless temporary retention is needed for debugging. Learning data is stored as text cards, not as the original image.

### Requirement: Voice deferred
Voice / audio message processing is out of scope for MVP. The system MUST NOT depend on STT in this change.
