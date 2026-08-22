"""aiogram application wiring (long polling)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

if TYPE_CHECKING:
    from spacedbro.llm.client import LLMClient

logger = logging.getLogger(__name__)


class BotApplication:
    """Owns the aiogram bot and dispatcher and runs long polling."""

    def __init__(self, token: str, llm_client: LLMClient) -> None:
        self._bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        # The LLM client is injected by construction (llm-router spec —
        # "Single client abstraction"); handlers receive it the same way
        # and never instantiate provider clients themselves. It is a
        # required dependency: the client is the only door to the LLM and
        # the extraction/generation handlers depend on it.
        self.llm_client = llm_client
        # Handlers (start, text, photo, callbacks, review) are added by later
        # tickets; the skeleton only needs the polling loop to boot.
        self._dispatcher = Dispatcher()

    async def run(self) -> None:
        logger.info("Starting Telegram long polling")
        await self._dispatcher.start_polling(self._bot)


def build_bot(token: str, llm_client: LLMClient) -> BotApplication:
    return BotApplication(token=token, llm_client=llm_client)
