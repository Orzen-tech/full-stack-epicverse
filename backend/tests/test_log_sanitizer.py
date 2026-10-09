"""Query-string values must never reach uvicorn's application logs.

Unit tests cover the sanitising rules and the logging.Filter contract; the integration
tests run a REAL uvicorn server (HTTP and WebSocket) in process, with uvicorn's own
default logging configuration and its real access formatter, on a loopback port. Nothing
here touches the database, Firebase or any external service.
"""
import asyncio
import io
import logging
import pathlib
import re

import httpx
import pytest
import uvicorn
import uvicorn.logging as uvicorn_logging
import websockets

from app.core import log_sanitizer
from app.core.log_sanitizer import (
    LOGGER_NAMES, REDACTED, QuerySanitizerFilter, install, sanitize_target, sanitize_text,
)

ACCESS_FMT = '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'


# ------------------------------------------------------------------ unit: rules
@pytest.mark.parametrize("target, expected", [
    # everything is redacted by default, including the usually harmless parameters
    ("/api/v1/ws/realtime?uid=U1&mode=Mode%201&session_id=S1&token=T1&listening=true&language=English",
     "/api/v1/ws/realtime?uid=***&mode=***&session_id=***&token=***&listening=***&language=***"),
    ("/health", "/health"),                                   # nothing to sanitise
    ("/x?", "/x"),                                            # empty query
    ("/x?&&", "/x"),                                          # only separators
    ("/x?a=1&a=2&a=3", "/x?a=***&a=***&a=***"),               # repeated parameters
    ("/x?eyJhbGciOi.payload.sig", "/x?***"),                  # bare key (a token pasted as a "key")
    ("/x?abc&b=2", "/x?***&b=***"),
    ("/x?token=%65%79%4a&k=a%26b", "/x?token=***&k=***"),     # percent-encoded values
    ("/x?to%6Ben=abc", "/x?***"),                             # percent-encoded NAME is not trusted either
    ("/x?" + "a" * 40 + "=v", "/x?***"),                      # over-long name
    ("/x?token=", "/x?token=***"),                            # empty value
    ("/x?t=a=b=c", "/x?t=***"),                               # '=' inside the value
    ("/x?a=1;b=2", "/x?a=***"),                               # ';' is not a separator for us: still one item
    ("/x?a=1#fragment", "/x?a=***"),                          # fragment dropped
    ("/x?1bad=1", "/x?***"),                                  # name must start with a letter/underscore
])
def test_sanitize_target_cases(target, expected):
    assert sanitize_target(target) == expected


def test_sanitize_target_caps_the_number_of_items_and_never_keeps_values():
    out = sanitize_target("/x?" + "&".join(f"p{i}=SECRET{i}" for i in range(100)))
    assert "SECRET" not in out
    assert out.count("=***") == 20 and out.endswith("&...")


@pytest.mark.parametrize("text, expected", [
    ('GET /a?x=SECRET HTTP/1.1', 'GET /a?x=*** HTTP/1.1'),
    ('"WebSocket /ws?uid=U&token=T" [accepted]', '"WebSocket /ws?uid=***&token=***" [accepted]'),
    ("https://host.example/p?token=T&b=2", "https://host.example/p?token=***&b=***"),
    ("two /a?x=1 and /b?y=2 targets", "two /a?x=*** and /b?y=*** targets"),
    ("connection open", "connection open"),
    ("What? Really", "What? Really"),                        # a question mark that is not a request target
    ("", ""),
])
def test_sanitize_text_cases(text, expected):
    assert sanitize_text(text) == expected


def test_a_huge_input_is_bounded_and_leaks_nothing():
    out = sanitize_text("GET /x?token=" + "A" * 1_000_000 + " HTTP/1.1")
    assert "AAAA" not in out and len(out) < 9000


# ------------------------------------------------------- unit: the logging filter
def _record(msg, args):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, msg, args, None)


def test_access_record_keeps_its_five_arguments_and_the_real_formatter_still_renders_it():
    rec = _record('%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5555", "GET", "/p?token=SECRET&mode=Mode%201", "1.1", 200))
    assert QuerySanitizerFilter().filter(rec) is True
    assert len(rec.args) == 5 and rec.args[:2] == ("127.0.0.1:5555", "GET") and rec.args[3:] == ("1.1", 200)
    line = uvicorn_logging.AccessFormatter(fmt=ACCESS_FMT, use_colors=False).format(rec)
    assert 'GET /p?token=*** HTTP/1.1' in line.replace("&mode=***", "") and "SECRET" not in line
    assert "&mode=***" in line and line.endswith("200 OK") and line.startswith("INFO:")


def test_websocket_record_keeps_the_client_tuple_and_other_non_string_arguments():
    rec = _record('%s - "WebSocket %s" [accepted]', (("10.0.0.1", 4242), "/ws?uid=U&token=SECRET"))
    QuerySanitizerFilter().filter(rec)
    assert rec.args[0] == ("10.0.0.1", 4242) and rec.args[1] == "/ws?uid=***&token=***"
    assert "SECRET" not in rec.getMessage()


def test_query_embedded_in_the_message_itself_is_sanitised_too():
    rec = _record("rejected GET /a?token=SECRET now", ())
    QuerySanitizerFilter().filter(rec)
    assert "SECRET" not in rec.getMessage() and "token=***" in rec.getMessage()


def test_dict_arguments_are_handled():
    rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "%(target)s", ({"target": "/a?x=SECRET"},), None)
    QuerySanitizerFilter().filter(rec)
    assert "SECRET" not in rec.getMessage()


@pytest.mark.parametrize("args", [None, (), (1, 2.5, None, object()), ("plain", "no query")])
def test_unexpected_or_clean_records_pass_through_unchanged(args):
    rec = _record("hello %s" if args else "hello", args or ())
    before = (rec.msg, rec.args)
    assert QuerySanitizerFilter().filter(rec) is True
    assert (rec.msg, rec.args) == before


def test_if_sanitising_itself_fails_the_record_is_withheld_not_leaked():
    class Explosive(str):
        def __contains__(self, item):
            raise RuntimeError("boom")

    rec = _record("%s", (Explosive("/a?token=SECRET"),))
    assert QuerySanitizerFilter().filter(rec) is True          # never raises into the logging call
    assert "SECRET" not in rec.getMessage() and rec.args == ()


def test_fail_closed_fallback_cuts_everything_after_the_question_mark(monkeypatch):
    monkeypatch.setattr(log_sanitizer, "sanitize_target", lambda t: (_ for _ in ()).throw(RuntimeError("x")))
    out = sanitize_text('GET /a?token=SECRET HTTP/1.1')
    assert "SECRET" not in out and out.startswith("GET /a")


# ------------------------------------------------------------ unit: installation
@pytest.fixture
def clean_loggers():
    """Remove our filter from uvicorn's loggers for the test, then restore what was there."""
    saved = {n: (list(logging.getLogger(n).filters), list(logging.getLogger(n).handlers)) for n in LOGGER_NAMES}
    for n in LOGGER_NAMES:
        lg = logging.getLogger(n)
        lg.filters[:] = [f for f in lg.filters if not isinstance(f, QuerySanitizerFilter)]
    yield
    for n, (filters, handlers) in saved.items():
        lg = logging.getLogger(n)
        lg.filters[:] = filters
        lg.handlers[:] = handlers


def _count(name):
    return sum(isinstance(f, QuerySanitizerFilter) for f in logging.getLogger(name).filters)


def test_install_is_idempotent(clean_loggers):
    assert install() == list(LOGGER_NAMES)
    assert install() == []                                      # second call attaches nothing
    assert [_count(n) for n in LOGGER_NAMES] == [1, 1]


def test_importing_the_real_app_installs_the_filter_exactly_once(clean_loggers):
    import importlib
    import app.main as app_main
    importlib.reload(app_main)
    importlib.reload(app_main)                                  # a reload must not stack filters
    assert [_count(n) for n in LOGGER_NAMES] == [1, 1]


def test_main_calls_install_before_creating_the_app():
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    assert "from app.core.log_sanitizer import install as install_log_sanitizer" in src
    assert src.index("install_log_sanitizer()") < src.index("app = FastAPI(")


# ------------------------------------------- integration: a REAL uvicorn server
async def _asgi(scope, receive, send):
    if scope["type"] == "http":
        await receive()
        status = 404 if scope["path"] == "/missing" else 200
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
    elif scope["type"] == "websocket":
        await receive()
        if scope["path"] == "/ws-reject":
            await send({"type": "websocket.close", "code": 1008})        # logged as '"WebSocket ..." 403'
        else:
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})


class _Server(uvicorn.Server):
    def install_signal_handlers(self):                                    # do not hijack the test process' signals
        pass


async def _with_server(install_filter: bool, traffic):
    """Start uvicorn with ITS default logging, then (like the app import does) attach our
    filter, capture the formatted log lines, run `traffic(port)` and return the captured text."""
    config = uvicorn.Config(_asgi, host="127.0.0.1", port=0, lifespan="off", http="h11", ws="websockets", log_level="info")
    server = _Server(config)                                             # Config() has configured uvicorn's loggers
    if install_filter:
        install()
    buf = io.StringIO()
    h_access, h_error = logging.StreamHandler(buf), logging.StreamHandler(buf)
    h_access.setFormatter(uvicorn_logging.AccessFormatter(fmt=ACCESS_FMT, use_colors=False))
    h_error.setFormatter(uvicorn_logging.DefaultFormatter(fmt="%(levelprefix)s %(message)s", use_colors=False))
    logging.getLogger("uvicorn.access").addHandler(h_access)
    logging.getLogger("uvicorn.error").addHandler(h_error)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started, "uvicorn did not start"
        await traffic(server.servers[0].sockets[0].getsockname()[1])
        await asyncio.sleep(0.2)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        logging.getLogger("uvicorn.access").removeHandler(h_access)
        logging.getLogger("uvicorn.error").removeHandler(h_error)
    return buf.getvalue()


SECRET_Q = "QTOKEN-7c1e5a9d"
SECRET_PATH_Q = "QUID-3b8f20aa"


async def _traffic(port):
    async with httpx.AsyncClient() as client:
        await client.get(f"http://127.0.0.1:{port}/ping?token={SECRET_Q}&mode=Mode%201&language=English")
        await client.get(f"http://127.0.0.1:{port}/ping?{SECRET_Q}-barekey")
        await client.get(f"http://127.0.0.1:{port}/missing?session_id={SECRET_Q}&x=1&x=2")
        await client.get(f"http://127.0.0.1:{port}/plain")
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?uid={SECRET_PATH_Q}&token={SECRET_Q}&mode=Mode%201") as ws:
        try:
            await ws.recv()
        except websockets.ConnectionClosed:
            pass
    with pytest.raises(Exception):                                         # handshake refused -> '"WebSocket ..." 403'
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws-reject?token={SECRET_Q}"):
            pass


async def test_real_uvicorn_http_and_websocket_logs_contain_no_query_values(clean_loggers):
    logs = await _with_server(True, _traffic)
    assert SECRET_Q not in logs and SECRET_PATH_Q not in logs and "Mode%201" not in logs and "English" not in logs
    # useful information is still there: method, path, status, severity, redacted names
    assert re.search(r'INFO:\s+\S+ - "GET /ping\?token=\*\*\*&mode=\*\*\*&language=\*\*\* HTTP/1\.1" 200 OK', logs), logs
    assert re.search(r'"GET /ping\?\*\*\* HTTP/1\.1" 200 OK', logs), logs
    assert re.search(r'"GET /missing\?session_id=\*\*\*&x=\*\*\*&x=\*\*\* HTTP/1\.1" 404 Not Found', logs), logs
    assert re.search(r'"GET /plain HTTP/1\.1" 200 OK', logs), logs                         # untouched when no query
    assert re.search(r'"WebSocket /ws\?uid=\*\*\*&token=\*\*\*&mode=\*\*\*" \[accepted\]', logs), logs
    assert re.search(r'"WebSocket /ws-reject\?token=\*\*\*" 403', logs), logs
    assert "Traceback" not in logs and "--- Logging error ---" not in logs


async def test_control_without_the_filter_the_real_uvicorn_does_log_the_values(clean_loggers):
    """Proves the integration test can detect the leak: same traffic, filter not installed."""
    logs = await _with_server(False, _traffic)
    assert SECRET_Q in logs and SECRET_PATH_Q in logs


async def test_real_server_keeps_working_when_every_log_record_goes_through_the_filter(clean_loggers):
    logs = await _with_server(True, _traffic)
    assert logs.count("[accepted]") == 1 and logs.count(" 200 OK") == 3 and REDACTED in logs
