"""Keeps URL query-string VALUES out of uvicorn's application logs.

uvicorn writes the request target, query string included, to two loggers:
  * ``uvicorn.access``  - ``"GET /path?query HTTP/1.1" 200``       (HTTP requests)
  * ``uvicorn.error``   - ``"WebSocket /path?query" [accepted]``   (WebSocket handshakes)
The line is written before the application decides anything, so a credential that a
caller (wrongly) puts in a query string would be logged even though the app ignores it.

This filter rewrites those records just before they are formatted:
  * method, path, status, client address and severity are kept;
  * query parameter NAMES are kept, every VALUE becomes ``***`` (no allow-list);
  * a bare key (``?abc``), an oddly shaped or very long name, or an empty item is replaced
    by ``***`` as a whole, so a secret passed as a "key" cannot slip through;
  * if sanitising ever fails, everything after the first ``?`` is cut (fail closed).

``record.args`` keeps its length: uvicorn's AccessFormatter unpacks exactly five values.

Not covered (outside the application's control): Cloud Run's own request log
(``run.googleapis.com/requests``), which records the full URL as received.
"""
import logging
import re

REDACTED = "***"
LOGGER_NAMES = ("uvicorn.access", "uvicorn.error")

_MAX_INPUT = 8192          # characters examined per string; the rest is dropped
_MAX_ITEMS = 20            # query items kept per target
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{0,31}")
# A request target inside a log string: "/path?query", also within "https://host/path?query".
_TARGET = re.compile(r"/[^\s\"'?#]*\?[^\s\"'#]*")


def sanitize_target(target: str) -> str:
    """Return ``target`` ("/path?query") with every query value redacted."""
    path, _, rest = target.partition("?")
    query = rest.partition("#")[0]          # fragments are never sent; drop defensively
    items = []
    for raw in query.split("&"):
        if raw == "":
            continue
        if len(items) >= _MAX_ITEMS:
            items.append("...")
            break
        name, sep, _value = raw.partition("=")
        items.append(f"{name}={REDACTED}" if sep and _NAME.fullmatch(name) else REDACTED)
    return path + ("?" + "&".join(items) if items else "")


def sanitize_text(text: str) -> str:
    """Redact the query string of every request target found inside ``text``."""
    clipped = text[:_MAX_INPUT] + ("..." if len(text) > _MAX_INPUT else "")
    try:
        return _TARGET.sub(lambda m: sanitize_target(m.group(0)), clipped)
    except Exception:                        # fail closed: drop everything after each '?'
        return re.sub(r"\?[^\s\"']*", "", clipped)


class QuerySanitizerFilter(logging.Filter):
    """Rewrites request targets in the message and in string arguments, in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str) and "?" in record.msg:
                record.msg = sanitize_text(record.msg)
            args = record.args
            if isinstance(args, tuple) and any(isinstance(a, str) and "?" in a for a in args):
                # Same length, same positions: uvicorn's AccessFormatter unpacks five values.
                record.args = tuple(sanitize_text(a) if isinstance(a, str) and "?" in a else a for a in args)
            elif isinstance(args, dict):
                record.args = {k: sanitize_text(v) if isinstance(v, str) and "?" in v else v for k, v in args.items()}
        except Exception:
            # Never let logging break a request, and never leave an unsanitised target behind.
            record.msg = "[log record withheld: could not be sanitised]"
            record.args = ()
        return True


def install() -> list:
    """Attach the filter to uvicorn's loggers. Safe to call more than once.

    Returns the names of the loggers it attached to on this call.
    """
    attached = []
    for name in LOGGER_NAMES:
        logger = logging.getLogger(name)
        if not any(isinstance(f, QuerySanitizerFilter) for f in logger.filters):
            logger.addFilter(QuerySanitizerFilter())
            attached.append(name)
    return attached
