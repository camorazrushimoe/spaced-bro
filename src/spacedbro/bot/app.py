"""aiogram application wiring (long polling).

BON-31 adds the add-flow wiring: the SQLite session + repositories, the
in-process context store + callback idempotency ledger, the LLM client,
the add-flow router (start / text / photo / voice / callbacks), and a
global error handler so no exception ever escapes to the Telegram API or
a user (design §9: "no stack traces to users").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from spacedbro.bot.handlers import build_add_flow_router
from spacedbro.bot.state import CallbackLedger, ContextStore
from spacedbro.clock import Clock
from spacedbro.db.engine import create_db_engine, create_session_factory
from spacedbro.db.repositories import ItemRepository, UserRepository

if TYPE_CHECKING:
    from spacedbro.llm.client import LLMClient

logger = logging.getLogger(__name__)


class BotApplication:
    """Owns the aiogram bot and dispatcher and runs long polling."""

    def __init__(
        self,
        token: str,
        llm_client: "LLMClient",
        database_url: str,
        clock: Clock,
    ) -> None:
        self._bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.clock = clock
        self.session_factory = create_session_factory(create_db_engine(database_url))
        # Dependencies shared by every handler (via workflow_data) — the
        # repositories are the only persistent state (BON-29), the LLM
        # client the only door to the LLM (llm-router spec).
        self.users = UserRepository(self.session_factory(), clock)
        self.items = ItemRepository(self.session_factory(), clock)
        self.store = ContextStore()
        self.ledger = CallbackLedger()
        self.llm_client = llm_client

        self._dispatcher = Dispatcher()
        self._dispatcher["clock"] = clock
        self._dispatcher["llm_client"] = llm_client
        self._dispatcher["users"] = self.users
        self._dispatcher["items"] = self.items
        self._dispatcher["store"] = self.store
        self._dispatcher["ledger"] = self.ledger
        self._dispatcher["bot"] = self._bot
        self._dispatcher.include_router(build_add_flow_router())
        self._dispatcher.errors.register(self._on_error, ErrorEvent)

    async def _on_error(self, event: ErrorEvent) -> None:
        """Global catch: log with context, never surface a stack trace."""
        logger.error(
            "Unhandled error while processing update: %s: %s",
            type(event.exception).__name__,
            event.exception,
            exc_info=True,
        )

    async def run(self) -> None:
        logger.info("Starting Telegram long polling")
        await self._dispatcher.start_polling(self._bot)


def build_bot(
    token: str,
    llm_client: "LLMClient",
    database_url: str,
    clock: Clock,
) -> BotApplication:
    return BotApplication(token, llm_client, database_url, clock)
