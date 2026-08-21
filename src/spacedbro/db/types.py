"""Custom column types.

``UTCDatetime`` closes the gap between "the application works in aware UTC"
and "SQLite stores datetimes as naive wall-clock text".
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDatetime(TypeDecorator):
    """DATETIME that round-trips as **aware UTC**.

    SQLite's ``DATETIME`` affinity stores wall-clock text and returns naive
    values, which silently misinterprets any non-UTC wall clock as UTC.
    This decorator:

    - on the way in: requires a timezone-aware datetime, normalizes to UTC,
      stores the naive UTC wall clock (the "all timestamps are UTC" rule);
    - on the way out: re-attaches UTC, so ORM instances — including plain
      ``session.get()`` reads outside the repositories — always expose
      aware-UTC datetimes.

    Naive input is rejected with :class:`ValueError` rather than guessed at.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(
                "UTCDatetime requires a timezone-aware datetime; "
                "all timestamps are stored as UTC"
            )
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)
