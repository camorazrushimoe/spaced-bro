"""LLM-backed services of the add flow (BON-31, design §3 + §5 + §2).

Thin, prompt-only adapters on top of the injected :class:`LLMClient`
(llm-router spec — "Single client abstraction"):

- :func:`extract_candidates` — text → 0–3 structured candidates
  (design §3: learning request → candidates; non-learning text → empty).
- :func:`generate_back` — one cheap one-line translation of ``front``
  **into the user's ``native_lang``** (design §5, step 3; learning-items
  spec "Back in native language").
- :func:`extract_from_image` — photo → 0–3 candidates via the vision
  endpoint (design §2 / media spec); the image is process-and-discard.

The domain error set of ``spacedbro.llm.errors`` (exactly five classes) is
the failure surface of this module — handlers map it to short, friendly
user-facing replies (design §9); stack traces never reach the user.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from spacedbro.bot.state import Candidate
from spacedbro.llm.client import LLMClient, LLMResponse, Message, ResponseFormat
from spacedbro.llm.errors import InvalidResponseError  # noqa: F401 (re-export)


#: The minimal LLM seam the services depend on. Production injects the
#: full ``LLMClient`` (which satisfies this structurally); tests inject a
#: small fake — the services never need the client's private surface.
@runtime_checkable
class LLMCaller(Protocol):
    async def complete(
        self,
        messages: list[Message],
        *,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    async def complete_with_vision(
        self,
        messages: list[Message],
        *,
        image_url: str,
        image_data_url: str | None = None,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

#: Max candidates per extraction (design §2 / §3: "1–3 candidates").
MAX_CANDIDATES = 3

_EXTRACT_SYSTEM = (
    "You are the extraction step of a language-learning flashcard bot. "
    "The user is learning {target_lang} (native language {native_lang}). "
    "Given the user's message, decide whether it is a request to learn "
    "vocabulary (a word, phrase, or text with extractable vocabulary). "
    "If it is, return 1 to {max} candidates: each candidate is the exact "
    "target-language word or short phrase (front) plus an optional brief "
    "context (the surrounding word or short phrase it was seen in). "
    "Do NOT translate; front must stay in the target language. "
    "If the message is a greeting, thanks, small talk, or anything with no "
    "learnable vocabulary, return an empty candidates list."
)

_BACK_SYSTEM = (
    "You are a language-learning flashcard bot. The target language is "
    "{target_lang}. Translate or define the given word or phrase in EXACTLY "
    "ONE short line, written in {native_lang} — the user's native language. "
    "No preamble, no examples, no quotation marks, no markdown. If the term "
    "is ambiguous, give the most common meaning."
)

_IMAGE_SYSTEM = (
    "You are the extraction step of a language-learning flashcard bot. "
    "The user sent a photo and is learning {target_lang} (native language "
    "{native_lang}). Read the image and return 1 to {max} vocabulary "
    "candidates: each candidate is an exact readable target-language word "
    "or short phrase (front) plus an optional brief context. Return an "
    "empty candidates list if the image has no useful readable text."
)

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "front": {"type": "string"},
                    "context": {"type": ["string", "null"]},
                },
                "required": ["front"],
            },
        }
    },
    "required": ["candidates"],
}

_BACK_SCHEMA = {
    "type": "object",
    "properties": {"back": {"type": "string"}},
    "required": ["back"],
}


def _clean_front(raw: object) -> str | None:
    """Normalize an LLM-provided front: strip, collapse whitespace, non-empty."""
    if not isinstance(raw, str):
        return None
    front = " ".join(raw.split())
    return front or None


def _candidates_from(data: object) -> list[Candidate]:
    """Structured payload → Candidate list (≤ MAX_CANDIDATES, non-empty fronts)."""
    out: list[Candidate] = []
    if not isinstance(data, dict):
        return out
    for entry in data.get("candidates") or []:
        if not isinstance(entry, dict):
            continue
        front = _clean_front(entry.get("front"))
        if front is None:
            continue
        context = entry.get("context")
        if context is not None:
            context = " ".join(context.split()) or None
        out.append(Candidate(front=front, context=context))
        if len(out) >= MAX_CANDIDATES:
            break
    return out


async def extract_candidates(
    client: LLMCaller,
    text: str,
    *,
    target_lang: str,
    native_lang: str,
) -> list[Candidate]:
    """Text → 0–3 candidates (design §3). Empty list = non-learning text.

    Raises the LLM domain error set on failure; the handler turns that into
    a short retry message (design §9) and creates nothing.
    """
    response = await client.complete(
        [
            Message(role="system", content=_EXTRACT_SYSTEM.format(target_lang=target_lang, native_lang=native_lang, max=MAX_CANDIDATES)),
            Message(role="user", content=text),
        ],
        response_format=ResponseFormat(schema=_EXTRACT_SCHEMA),
        temperature=0.0,
        max_tokens=300,
    )
    return _candidates_from(response.structured)


async def generate_back(
    client: LLMCaller,
    front: str,
    context: str | None,
    *,
    target_lang: str,
    native_lang: str,
) -> str:
    """One-line ``back`` in the user's native language (design §5, step 3).

    Raises the LLM domain error set on failure; the handler then shows a
    short error and does NOT persist anything (learning-items spec
    "Back LLM failure").
    """
    user_line = front if context is None else f"{front} (context: {context})"
    response = await client.complete(
        [
            Message(role="system", content=_BACK_SYSTEM.format(native_lang=native_lang, target_lang=target_lang)),
            Message(role="user", content=user_line),
        ],
        response_format=ResponseFormat(schema=_BACK_SCHEMA),
        temperature=0.2,
        max_tokens=80,
    )
    data = response.structured
    back = data.get("back") if isinstance(data, dict) else None
    if not isinstance(back, str):
        raise InvalidResponseError(f"back generation returned no 'back': {str(response.content)[:120]}")
    back = " ".join(back.split())
    if not back:
        raise InvalidResponseError("back generation returned an empty line")
    return back


async def extract_from_image(
    client: LLMCaller,
    image_bytes: bytes,
    *,
    target_lang: str,
    native_lang: str,
) -> list[Candidate]:
    """Photo → 0–3 candidates (design §2, media spec "Vision-based extraction").

    The image bytes are sent to the vision endpoint and then discarded —
    never stored (media spec "No permanent image storage (default)").
    An empty result means "nothing readable" and the handler replies with a
    short message (design §9 "Unreadable image").
    """
    import base64
    import mimetypes

    mime = mimetypes.guess_type("photo.jpg")[0] or "image/jpeg"
    data_url = f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")
    response = await client.complete_with_vision(
        [
            Message(role="system", content=_IMAGE_SYSTEM.format(target_lang=target_lang, native_lang=native_lang, max=MAX_CANDIDATES)),
            Message(role="user", content="Extract vocabulary candidates from this image."),
        ],
        image_url=data_url,
        response_format=ResponseFormat(schema=_EXTRACT_SCHEMA),
        temperature=0.0,
        max_tokens=300,
    )
    return _candidates_from(response.structured)
