from __future__ import annotations

import os
import ssl
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_CALENDAR_BYTES = 5 * 1024 * 1024
MACOS_CA_BUNDLE = Path("/etc/ssl/cert.pem")


class CalendarPollError(RuntimeError):
    """A safe-to-display polling error that never includes the private URL."""


def verified_tls_context() -> ssl.SSLContext:
    """Build a verified context, including Homebrew Python's macOS fallback."""
    paths = ssl.get_default_verify_paths()
    if sys.platform == "darwin" and not paths.cafile and MACOS_CA_BUNDLE.is_file():
        return ssl.create_default_context(cafile=str(MACOS_CA_BUNDLE))
    return ssl.create_default_context()


def calendar_url_from_environment(variable_name: str) -> str:
    url = os.environ.get(variable_name, "").strip()
    if not url:
        raise CalendarPollError(
            f"Set {variable_name} to the private Airbnb calendar export URL."
        )
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CalendarPollError(
            f"{variable_name} must contain a valid HTTPS calendar URL."
        )
    return url


def fetch_private_ical(
    url: str,
    timeout_seconds: int = 20,
    opener: Callable[..., object] = urlopen,
) -> str:
    """Fetch a private calendar without persisting or displaying its URL."""
    if urlsplit(url).scheme != "https":
        raise CalendarPollError("The private calendar URL must use HTTPS.")
    request = Request(url, headers={
        "Accept": "text/calendar, text/plain;q=0.9",
        "User-Agent": "host-ops/0.1 calendar-poller",
    })
    try:
        with opener(
            request,
            timeout=timeout_seconds,
            context=verified_tls_context(),
        ) as response:
            if urlsplit(response.geturl()).scheme != "https":
                raise CalendarPollError("Calendar endpoint redirected away from HTTPS.")
            content_type = response.headers.get_content_type()
            if content_type not in {
                "text/calendar", "text/plain", "application/octet-stream"
            }:
                raise CalendarPollError(
                    "Calendar endpoint returned an unexpected content type."
                )
            raw = response.read(MAX_CALENDAR_BYTES + 1)
    except CalendarPollError:
        raise
    except Exception as error:
        raise CalendarPollError(
            f"Could not fetch the private calendar ({type(error).__name__})."
        ) from error
    if len(raw) > MAX_CALENDAR_BYTES:
        raise CalendarPollError("Calendar response exceeded the 5 MB safety limit.")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CalendarPollError("Calendar response was not valid UTF-8.") from error
    if "BEGIN:VCALENDAR" not in content:
        raise CalendarPollError("Calendar endpoint did not return an iCalendar document.")
    return content
