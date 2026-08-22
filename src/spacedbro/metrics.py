"""Process metrics (BON-34, tasks.md §7 "Logs/metrics").

Basic metrics for the MVP ops layer (design §10): **LLM call counts** and
**item counts**, plus the user count that the item gauges live next to.
One object, zero dependencies, no external exporter — scraped over HTTP by
the health server's ``/metrics`` endpoint (Prometheus text format).

Design notes:

- The :class:`Metrics` instance is constructed **once** by the entrypoint
  and injected, like the clock: into the LLM client (call counts) and the
  health app (gauges + rendering). The LLM client only knows two seams —
  :meth:`Metrics.record_llm_call` (success) and :meth:`Metrics.record_llm_error`
  (failure) — and never formats names or labels.
- Error classification (:func:`classify_llm_error`) maps the five
  ``spacedbro.llm.errors`` domain errors onto stable label values, with an
  ``error`` bucket for anything unforeseen (a future sixth class can never
  crash a call).
- Gauges are **best-effort**: without a bound session factory they hold
  zero, and a refresh that cannot open the database keeps the last known
  value. Metrics must never take the health endpoint or the bot down.
- Single event loop, no locks: the MVP runs exactly one process (design
  §10 single-instance assumption), and dict updates are atomic enough for
  this use under CPython.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from spacedbro.llm.errors import (
    InvalidResponseError,
    LLMError,
    ProviderUnavailableError,
    RateLimitError,
    TimeoutError,
    VisionNotSupportedError,
)

if TYPE_CHECKING:
    from spacedbro.clock import Clock

logger = logging.getLogger(__name__)

#: LLM call kinds as counted (mirrors the client's two public methods).
LLM_KIND_TEXT = "text"
LLM_KIND_VISION = "vision"


def classify_llm_error(exc: LLMError) -> str:
    """Stable metric label for an LLM domain error.

    Order matters: ``ProviderUnavailableError`` carries no subclass
    relations with the others, but the check is written explicitly so the
    mapping is one-to-one with the five-class domain set.
    """
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, RateLimitError):
        return "rate_limited"
    if isinstance(exc, ProviderUnavailableError):
        return "provider_unavailable"
    if isinstance(exc, InvalidResponseError):
        return "invalid_response"
    if isinstance(exc, VisionNotSupportedError):
        return "vision_unsupported"
    return "error"


class Metrics:
    """Counters + gauges rendered as Prometheus text (``/metrics`` body).

    Counters (monotonic since process start):

    - ``spacedbro_llm_calls_total{kind, outcome="ok"}``
    - ``spacedbro_llm_errors_total{kind, error}``

    Gauges (refreshed from the database on demand, best-effort):

    - ``spacedbro_users_total``
    - ``spacedbro_items_total``
    - ``spacedbro_items_due_total``
    """

    def __init__(self, session_factory: "Callable[[], Any] | None" = None) -> None:
        self._llm_ok: dict[tuple[str, str], int] = {}
        self._llm_errors: dict[tuple[str, str], int] = {}
        self._session_factory = session_factory
        self._users_total = 0
        self._items_total = 0
        self._items_due_total = 0
        self._clock: "Clock | None" = None

    # --- LLM counters (called by the LLM client) ---------------------------

    def record_llm_call(self, kind: str, *, ok: bool, error: "LLMError | None" = None) -> None:
        """Count one completed LLM call (a success or a final failure)."""
        if ok:
            key = (kind, "ok")
            self._llm_ok[key] = self._llm_ok.get(key, 0) + 1
        else:
            error_class = classify_llm_error(error) if error is not None else "error"
            key = (kind, error_class)
            self._llm_errors[key] = self._llm_errors.get(key, 0) + 1

    # Bind the process clock for due-at-now gauge refreshes.
    def bind_clock(self, clock: "Clock") -> None:
        self._clock = clock

    def bind_session_factory(self, session_factory: "Callable[[], Any]") -> None:
        """Bind the bot's session factory so gauges read the live database."""
        self._session_factory = session_factory

    def refresh_gauges(self) -> None:
        """Recompute the item/user gauges from the database.

        Best-effort by contract: no session factory → no-op (gauges hold
        their last value); unreachable database → keep last value and log
        at debug. This never raises.
        """
        if self._session_factory is None:
            return
        try:
            from sqlalchemy import func, select

            from spacedbro.db.models import LearningItem, User

            with self._session_factory() as session:
                users_total = int(session.execute(select(func.count()).select_from(User)).scalar_one())
                items_total = int(
                    session.execute(select(func.count()).select_from(LearningItem)).scalar_one()
                )
                now = self._clock.utc_now() if self._clock is not None else None
                due_stmt = select(func.count()).select_from(LearningItem)
                if now is not None:
                    due_stmt = due_stmt.where(LearningItem.next_review_at <= now)
                items_due = int(session.execute(due_stmt).scalar_one())
        except Exception:
            logger.debug("metrics gauge refresh failed; keeping last values", exc_info=True)
            return
        self._users_total = users_total
        self._items_total = items_total
        self._items_due_total = items_due

    # --- Rendering ------------------------------------------------------------

    def _labels(self, labels: tuple[tuple[str, str], ...]) -> str:
        return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"

    def render(self) -> str:
        """Prometheus text exposition (stable and sorted — grep-able)."""
        lines: list[str] = []

        def gauge(name: str, help_text: str, value: int) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        gauge(
            "spacedbro_users_total",
            "Total user profiles (refreshed on scrape).",
            self._users_total,
        )
        gauge(
            "spacedbro_items_total",
            "Total learning items (refreshed on scrape).",
            self._items_total,
        )
        gauge(
            "spacedbro_items_due_total",
            "Learning items due now (refreshed on scrape).",
            self._items_due_total,
        )

        if self._llm_ok:
            lines.append("# HELP spacedbro_llm_calls_total Successful LLM calls by kind.")
            lines.append("# TYPE spacedbro_llm_calls_total counter")
            for (kind, outcome) in sorted(self._llm_ok):
                lines.append(
                    f'spacedbro_llm_calls_total{self._labels((("kind", kind), ("outcome", outcome)))}'
                    f" {self._llm_ok[(kind, outcome)]}"
                )

        if self._llm_errors:
            lines.append(
                "# HELP spacedbro_llm_errors_total Failed LLM calls by kind and error class."
            )
            lines.append("# TYPE spacedbro_llm_errors_total counter")
            for (kind, error_class) in sorted(self._llm_errors):
                lines.append(
                    f"spacedbro_llm_errors_total"
                    f"{self._labels((('kind', kind), ('error', error_class)))}"
                    f" {self._llm_errors[(kind, error_class)]}"
                )

        return "\n".join(lines) + "\n"
