"""Shared fixtures for the add-flow handler tests (BON-31).

Everything runs against the public seam the ticket defines:
constructed aiogram objects (Message / CallbackQuery) fed directly into the
module-level handler functions with explicit keyword dependencies, backed
by real in-memory SQLite repositories (BON-29) and a fake LLM client. No
network, no live bot, no wall clock.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PhotoSize,
    User,
    Voice,
)
from sqlalchemy.orm import Session

from spacedbro.bot.state import CallbackLedger, ContextStore
from spacedbro.db.base import Base
from spacedbro.db.engine import create_db_engine
from spacedbro.db.models import LearningItem, User as UserModel
from spacedbro.db.repositories import ItemRepository, UserRepository
from spacedbro.llm.client import LLMResponse, Message as LLMMessage, ResponseFormat

NOW = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)
TG_ID = 42


class SteadyClock:
    """Deterministic clock (fixed UTC now) for handler/repo time reads.

    ``advance`` moves the frozen time forward, so tests can model real
    elapsed time between interactions (e.g. a due card after a rating).
    """

    def __init__(self, now: datetime) -> None:
        self._now = now

    def utc_now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> datetime:
        from datetime import timedelta

        self._now = self._now + timedelta(**kwargs)
        return self._now

    async def sleep(self, seconds: float) -> None:  # pragma: no cover
        return None


# --- Fake LLM client (the LLMCaller seam) ---------------------------------------


class FakeLLM:
    """Scriptable stand-in for the LLM seam.

    - ``texts``: queued one-line backs for generate_back calls.
    - ``extracts``: queued candidate lists for text extraction.
    - ``extract_errors`` / ``vision_errors``: exception to raise on the next call.
    - ``vision``: queued candidate lists for the photo path.
    """

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.extracts: list[list[dict[str, Any]]] = []
        self.extract_errors: list[Exception] = []
        self.back_errors: list[Exception] = []
        self.vision: list[list[dict[str, Any]]] = []
        self.vision_errors: list[Exception] = []
        self.complete_calls: list[list[LLMMessage]] = []
        self.vision_calls: list[dict[str, Any]] = []

    def _back(self) -> LLMResponse:
        if self.back_errors:
            raise self.back_errors.pop(0)
        text = self.texts.pop(0) if self.texts else "default back"
        return LLMResponse(
            content=json.dumps({"back": text}),
            model="fake",
            finish_reason="stop",
            structured={"back": text},
        )

    def _extract(self) -> LLMResponse:
        if self.extract_errors:
            raise self.extract_errors.pop(0)
        candidates = self.extracts.pop(0) if self.extracts else []
        return LLMResponse(
            content=json.dumps({"candidates": candidates}),
            model="fake",
            finish_reason="stop",
            structured={"candidates": candidates},
        )

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.complete_calls.append(messages)
        if response_format is not None and "back" in dict(response_format.schema).get("properties", {}):
            return self._back()
        return self._extract()

    async def complete_with_vision(
        self,
        messages: list[LLMMessage],
        *,
        image_url: str,
        image_data_url: str | None = None,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.vision_calls.append({"image_url": image_url})
        if self.vision_errors:
            raise self.vision_errors.pop(0)
        candidates = self.vision.pop(0) if self.vision else []
        return LLMResponse(
            content=json.dumps({"candidates": candidates}),
            model="fake",
            finish_reason="stop",
            structured={"candidates": candidates},
        )


# --- Fake bot (download seam for photos) ------------------------------------------


class FakeBot:
    """Download-seam stand-in matching aiogram 3.x: ``download_file``
    returns an in-memory BinaryIO stream (or None when no file)."""

    def __init__(self, image_bytes: bytes = b"fake-image-bytes") -> None:
        self.image_bytes = image_bytes
        self.downloads = 0

    async def get_file(self, file_id: str) -> Any:
        class _File:
            file_path = f"/fake/{file_id}"

        return _File()

    async def download_file(self, file_path: str) -> Any:
        self.downloads += 1
        if not self.image_bytes:
            return None
        return io.BytesIO(self.image_bytes)


# --- Message / callback builders ----------------------------------------------------


def make_message(
    *,
    text: str | None = None,
    tg_id: int = TG_ID,
    photo: list[PhotoSize] | None = None,
    voice: Voice | None = None,
    message_id: int = 1,
) -> Message:
    return Message(
        message_id=message_id,
        date=NOW,
        chat=Chat(id=tg_id, type="private"),
        from_user=User(id=tg_id, is_bot=False, first_name="Tester"),
        text=text,
        photo=photo,
        voice=voice,
    )


def make_photo_message(tg_id: int = TG_ID, message_id: int = 1) -> Message:
    return make_message(
        tg_id=tg_id,
        message_id=message_id,
        photo=[
            PhotoSize(file_id="p-small", file_unique_id="u-small", width=90, height=60),
            PhotoSize(file_id="p-large", file_unique_id="u-large", width=800, height=600),
        ],
    )


def make_callback(*, data: str, tg_id: int = TG_ID, message: Any = None) -> CallbackQuery:
    """Build a CallbackQuery; ``message`` may be a real Message or a plain
    recording stand-in (handlers only call ``.answer`` on it)."""
    return CallbackQuery(
        id=hashlib.md5(data.encode()).hexdigest()[:20],
        from_user=User(id=tg_id, is_bot=False, first_name="Tester"),
        chat_instance="ci",
        message=message,
        data=data,
    )


# --- Dependency container -----------------------------------------------------------


def make_deps(session: Session, fake_llm: FakeLLM, bot: Any | None = None, now: datetime = NOW) -> dict[str, Any]:
    """The handler dependency set (mirrors BotApplication wiring)."""
    clock = SteadyClock(now)
    return {
        "clock": clock,
        "llm_client": fake_llm,
        "users": UserRepository(session, clock),
        "items": ItemRepository(session, clock),
        "store": ContextStore(),
        "ledger": CallbackLedger(),
        "bot": bot if bot is not None else FakeBot(),
    }


# --- DB access helpers ---------------------------------------------------------------


@pytest.fixture
def session():
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


def get_user(session: Session, tg_id: int = TG_ID) -> UserModel:
    return session.query(UserModel).filter(UserModel.telegram_id == tg_id).one()


def get_items(session: Session, tg_id: int = TG_ID) -> list[LearningItem]:
    user = get_user(session, tg_id)
    return list(session.query(LearningItem).filter(LearningItem.user_id == user.id))


def make_callback_for_recording(*, data: str, tg_id: int = TG_ID, recording: Any) -> CallbackQuery:
    return CallbackQuery(
        id=hashlib.md5(data.encode()).hexdigest()[:20],
        from_user=User(id=tg_id, is_bot=False, first_name="Tester"),
        chat_instance="ci",
        message=recording,
        data=data,
    )
