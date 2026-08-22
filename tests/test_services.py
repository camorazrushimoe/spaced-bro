"""Service-layer tests (BON-31, design §3/§5/§2) against the LLMClient seam.

The services are thin adapters over the injected LLM client, so every test
runs against a fake client (stub transport equivalent — no network, no
provider): prompt shape, structured-output parsing, candidate cleaning, and
the failure surface (the five LLM domain errors).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from spacedbro.bot.services import (
    MAX_CANDIDATES,
    extract_candidates,
    extract_from_image,
    generate_back,
)
from spacedbro.clock import FrozenClock
from spacedbro.llm.client import LLMResponse, Message, ResponseFormat
from spacedbro.llm.config import LLMSettings
from spacedbro.llm.errors import (
    InvalidResponseError,
    LLMError,
    TimeoutError,
)

_T0 = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)


class FakeLLM:
    """Minimal LLMClient stand-in recording calls and replaying canned responses."""

    def __init__(
        self,
        *,
        structured: dict | None = None,
        error: Exception | None = None,
        vision_structured: dict | None = None,
    ) -> None:
        self.structured = structured
        self.error = error
        self.vision_structured = vision_structured
        self.calls: list[dict] = []
        # Satisfy the LLMClient structural protocol (private attrs on the class).
        self._settings = None
        self._transport = None
        self._clock = None
        self._random = None
        self._url = ""
        self._headers = {}
        self._log_prompts = False

    def _respond(self, structured: dict) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(structured),
            model="fake",
            finish_reason="stop",
            structured=structured,
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {"messages": messages, "response_format": response_format, "max_tokens": max_tokens}
        )
        if self.error is not None:
            raise self.error
        assert response_format is not None, "services always request structured output"
        return self._respond(self.structured or {})

    async def complete_with_vision(
        self,
        messages: list[Message],
        *,
        image_url: str,
        image_data_url: str | None = None,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append({"vision_image_url": image_url, "messages": messages})
        if self.error is not None:
            raise self.error
        return self._respond(self.vision_structured or self.structured or {})


# --- extract_candidates (design §3) --------------------------------------------


async def test_extract_returns_candidates_with_context() -> None:
    fake = FakeLLM(
        structured={
            "candidates": [
                {"front": "apple", "context": "I ate an apple"},
                {"front": "eat", "context": None},
            ]
        }
    )
    out = await extract_candidates(
        fake, "I ate an apple", target_lang="en", native_lang="ru"
    )
    assert [c.front for c in out] == ["apple", "eat"]
    assert out[0].context == "I ate an apple"
    assert out[1].context is None


async def test_extract_ignores_non_learning_text() -> None:
    fake = FakeLLM(structured={"candidates": []})
    out = await extract_candidates(fake, "hi thanks bro", target_lang="en", native_lang="ru")
    assert out == []


async def test_extract_caps_at_three_and_cleans_fronts() -> None:
    fake = FakeLLM(
        structured={
            "candidates": [
                {"front": "  a  b "},
                {"front": ""},
                {"front": "c"},
                {"front": "d"},
                {"front": "e"},
            ]
        }
    )
    out = await extract_candidates(fake, "x", target_lang="en", native_lang="ru")
    assert [c.front for c in out] == ["a b", "c", "d"]
    assert len(out) <= MAX_CANDIDATES


async def test_extract_rejects_malformed_entries() -> None:
    fake = FakeLLM(
        structured={"candidates": [{"context": "no front"}, "junk", {"front": 42}, {"front": "ok"}]}
    )
    out = await extract_candidates(fake, "x", target_lang="en", native_lang="ru")
    assert [c.front for c in out] == ["ok"]


async def test_extract_uses_structured_output_and_low_temp() -> None:
    fake = FakeLLM(structured={"candidates": []})
    await extract_candidates(fake, "word", target_lang="de", native_lang="ru")
    call = fake.calls[0]
    assert call["response_format"] is not None
    assert dict(call["response_format"].schema)["required"] == ["candidates"]
    assert call["max_tokens"] == 300
    # The prompt names both languages (target + native) per design §3.
    system = call["messages"][0].content
    assert "de" in system and "ru" in system
    assert call["messages"][1].content == "word"


async def test_extract_propagates_llm_domain_errors() -> None:
    fake = FakeLLM(error=TimeoutError("deadline"))
    with pytest.raises(TimeoutError):
        await extract_candidates(fake, "word", target_lang="en", native_lang="ru")


# --- generate_back (design §5 step 3: one line into native_lang) ----------------


async def test_back_prompt_targets_native_lang_and_single_line() -> None:
    fake = FakeLLM(structured={"back": "яблоко"})
    out = await generate_back(
        fake, "apple", "I ate an apple", target_lang="en", native_lang="ru"
    )
    assert out == "яблоко"
    call = fake.calls[0]
    system = call["messages"][0].content
    assert "ru" in system
    user_line = call["messages"][1].content
    assert "apple" in user_line and "I ate an apple" in user_line


async def test_back_without_context_sends_front_only() -> None:
    fake = FakeLLM(structured={"back": "to eat"})
    await generate_back(fake, "eat", None, target_lang="en", native_lang="en")
    assert fake.calls[0]["messages"][1].content == "eat"


async def test_back_collapses_whitespace() -> None:
    fake = FakeLLM(structured={"back": "  привет ,   брат  "})
    assert (
        await generate_back(fake, "hello", None, target_lang="en", native_lang="ru")
        == "привет , брат"
    )


async def test_back_missing_field_is_an_error() -> None:
    fake = FakeLLM(structured={})
    with pytest.raises(LLMError):
        await generate_back(fake, "apple", None, target_lang="en", native_lang="ru")


async def test_back_empty_line_is_an_error() -> None:
    fake = FakeLLM(structured={"back": "   "})
    with pytest.raises(LLMError):
        await generate_back(fake, "apple", None, target_lang="en", native_lang="ru")


async def test_back_provider_failure_propagates() -> None:
    fake = FakeLLM(error=InvalidResponseError("HTTP 401"))
    with pytest.raises(InvalidResponseError):
        await generate_back(fake, "apple", None, target_lang="en", native_lang="ru")


# --- extract_from_image (design §2: process-and-discard) ------------------------


async def test_vision_candidates_and_data_url() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload"
    fake = FakeLLM(vision_structured={"candidates": [{"front": "STOP"}]})
    out = await extract_from_image(
        fake, png, target_lang="en", native_lang="ru"
    )
    assert [c.front for c in out] == ["STOP"]
    call = fake.calls[0]
    # The data URL must declare the ACTUAL content type of the bytes —
    # Telegram photos are PNG/WEBP, and a hardcoded "photo.jpg" would
    # mislabel them (media spec "Vision-based extraction").
    assert call["vision_image_url"].startswith("data:image/png;"), call["vision_image_url"]
    # The raw bytes never leave the function (process-and-discard).
    assert b"fake-png-payload" not in call["vision_image_url"].encode("latin-1", "ignore")


async def test_vision_jpeg_photo_gets_jpeg_content_type() -> None:
    fake = FakeLLM(vision_structured={"candidates": []})
    await extract_from_image(fake, b"\xff\xd8\xff jpeg", target_lang="en", native_lang="ru")
    assert fake.calls[0]["vision_image_url"].startswith("data:image/jpeg;")


async def test_vision_empty_means_unreadable_image() -> None:
    fake = FakeLLM(vision_structured={"candidates": []})
    out = await extract_from_image(fake, b"img", target_lang="en", native_lang="ru")
    assert out == []


async def test_vision_not_supported_propagates() -> None:
    from spacedbro.llm.errors import VisionNotSupportedError

    fake = FakeLLM(error=VisionNotSupportedError("HTTP 404 local"))
    with pytest.raises(VisionNotSupportedError):
        await extract_from_image(fake, b"img", target_lang="en", native_lang="ru")


# --- Settings sanity: the client contract the services rely on -------------------


def test_llm_settings_defaults_for_development() -> None:
    from spacedbro.llm.config import load_llm_settings

    s = load_llm_settings({"APP_ENV": "development"})
    assert s.provider == "openai_compatible"
    assert s.timeout_seconds == 30
    assert s.max_retries == 2
    frozen = FrozenClock(_T0)
    assert frozen.utc_now() == _T0
