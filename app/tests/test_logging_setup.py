"""Secret redaction + HTTP-client log quieting (app/logging_setup.py).

Regression for the 2026-11-05 incident: httpx logs every request URL at INFO,
and provider keys (Polygon ``apiKey``, BEA ``UserID``) are query parameters, so
the first real deployment wrote the Polygon key to CloudWatch.
"""
from __future__ import annotations

import io
import logging

import httpx
import pytest

from app.logging_setup import LOG_FORMAT, RedactingFormatter, configure_logging, redact

SECRET = "sk_test_NOT_A_REAL_KEY_123"


@pytest.fixture
def captured_root():
    """Point the root logger at a StringIO via configure_logging, then restore
    whatever pytest had installed so other tests are unaffected."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_levels = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    stream = io.StringIO()
    configure_logging(stream=stream, force=True)
    try:
        yield stream
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for n, lvl in saved_levels.items():
            logging.getLogger(n).setLevel(lvl)


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"GET https://api.polygon.io/v3/x?ticker=XLE&apiKey={SECRET}", "GET https://api.polygon.io/v3/x?ticker=XLE&apiKey=***"),
        (f"https://api.polygon.io/v3/x?apiKey={SECRET}&limit=1000", "https://api.polygon.io/v3/x?apiKey=***&limit=1000"),
        (f"https://apps.bea.gov/api/data?UserID={SECRET}&method=GetData", "https://apps.bea.gov/api/data?UserID=***&method=GetData"),
        (f"registrationkey={SECRET}", "registrationkey=***"),
        (f"api_key={SECRET} refresh_token={SECRET}", "api_key=*** refresh_token=***"),
        ("https://x/y?ticker=AAPL&limit=5", "https://x/y?ticker=AAPL&limit=5"),  # nothing secret: untouched
        ("primary_key=7 cache_key=abc", "primary_key=7 cache_key=abc"),  # \b: *_key names are not matched
        ("no query string at all", "no query string at all"),
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_formatter_redacts_message_and_traceback() -> None:
    fmt = RedactingFormatter(LOG_FORMAT)
    try:
        raise RuntimeError(f"Client error '429' for url 'https://api.polygon.io/v3/x?apiKey={SECRET}'")
    except RuntimeError:
        import sys
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed %s", (f"apiKey={SECRET}",), sys.exc_info())
    out = fmt.format(rec)
    assert SECRET not in out
    assert "apiKey=***" in out
    assert "RuntimeError" in out  # the traceback is still there, only the secret is gone


def test_httpx_request_line_is_not_logged_and_nothing_secret_reaches_the_stream(captured_root: io.StringIO) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        client.get("https://api.polygon.io/v3/reference/tickers", params={"ticker": "XLE", "apiKey": SECRET})

    logged = captured_root.getvalue()
    assert SECRET not in logged
    assert "HTTP Request" not in logged  # httpx's INFO line is quieted, not just redacted


def test_app_loggers_still_reach_the_stream_with_secrets_redacted(captured_root: io.StringIO) -> None:
    logging.getLogger("app.example").info("calling https://x/y?apiKey=%s", SECRET)
    logged = captured_root.getvalue()
    assert "calling https://x/y?apiKey=***" in logged
    assert SECRET not in logged


def test_httpx_and_httpcore_are_warning_level_after_configure(captured_root: io.StringIO) -> None:
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_main_api_installs_the_configuration() -> None:
    """Importing the real app must leave the HTTP clients quiet; this is what
    protects the deployed containers, not just the helper in isolation."""
    import app.main_api  # noqa: F401

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
