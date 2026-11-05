"""Process-wide logging setup: one format, quiet HTTP clients, no secrets.

Why this exists (2026-11-05): ``httpx`` logs every request URL at INFO, and
several providers carry their key as a query parameter (Polygon ``apiKey``, BEA
``UserID``). The first real deployment shipped the Polygon key to CloudWatch,
where it stays for the log group's retention. Two layers, deliberately:

1. ``httpx`` / ``httpcore`` are held at WARNING, so the per-request URL line is
   never produced.
2. Every line the root handler formats passes through :func:`redact`, so a key
   that reaches a log some other way (an exception message such as httpx's
   ``Client error '429' for url '...apiKey=...'``, a traceback, a debug line) is
   masked as well.

Redaction matches **parameter names**, not values, so it works for a key we have
never seen. Add a name to ``_SECRET_PARAM`` when a new provider puts a credential
in a URL. Names are matched on a word boundary, so ``primary_key=`` and
``cache_key=`` are left alone.

Called once, at import time of ``app.main_api`` (where ``logging.basicConfig``
used to be). Scripts that want the same behaviour can call it themselves.
"""
from __future__ import annotations

import logging
import re
from typing import TextIO

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Loggers that print full request URLs at INFO.
_QUIET_AT_WARNING = ("httpx", "httpcore")

# name=value pairs whose value is a credential. Value runs to the next
# delimiter used in URLs, query strings and log text.
_SECRET_PARAM = re.compile(
    r"(?i)\b(api[_-]?key|userid|registrationkey|access_token|refresh_token|client_secret|password)=([^&\s\"'#]+)"
)


def redact(text: str) -> str:
    """Mask the value of every known credential parameter in ``text``."""
    return _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=***", text)


class RedactingFormatter(logging.Formatter):
    """Redacts the final formatted record: message, args and traceback alike."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(
    level: int = logging.INFO,
    *,
    stream: TextIO | None = None,
    force: bool = False,
) -> None:
    """Install the root handler and quiet the HTTP clients.

    ``force`` replaces handlers that are already installed (tests only); like
    ``logging.basicConfig`` it is otherwise a no-op when the root logger is
    already configured. The logger levels are applied either way.
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(LOG_FORMAT))
    logging.basicConfig(level=level, handlers=[handler], force=force)
    for name in _QUIET_AT_WARNING:
        logging.getLogger(name).setLevel(logging.WARNING)
