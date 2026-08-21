"""aiogram application wiring (long polling)."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)


class BotApplication:
    """Owns the aiogram bot and dispatcher and runs long polling."""

    def __init__(self, token: str) -> None:
        self._bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        # Handlers (start, text, photo, callbacks, review) are added by later
        # tickets; the skeleton only needs the polling loop to boot.
        self._dispatcher = Dispatcher()

    async def run(self) -> None:
        logger.info("Starting Telegram long polling")
        await self._dispatcher.start_polling(self._bot)


def build_bot(token: str) -> BotApplication:
    return BotApplication(token=token)
