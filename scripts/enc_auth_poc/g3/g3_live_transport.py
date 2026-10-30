"""G3 Phase 2 live transport (standard library only): the one place that can open a real HTTPS connection.

It cannot be created without a LiveApproval (see g3_guard), it asks that approval for permission immediately before EVERY connection
(each approved request can be used once, so there is no retry), and it uses: HTTPS to an approved host on port 443; normal certificate and
hostname verification with TLS >= 1.2; no redirect handling (a 3xx is returned as is and rejected by the client); no proxy or environment
inheritance (http.client reads neither); split connect/read timeouts; a hard cap on the response size. Exceptions are reduced to a fixed
cause code: no message, URL, header or body from the HTTP stack ever leaves this module.
"""
import http.client
import socket
import ssl
from urllib.parse import urlencode, urlsplit

import g3_guard as guard
from g3_rest_client import TransportFailure, TransportResponse


def make_tls_context():
    """System trust store, certificate verification ON, hostname checking ON, TLS 1.2 or newer. Never relaxed."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
        raise guard.GuardError("tls_verification_disabled")
    return context


class LiveTransport:
    def __init__(self, approval, connection_factory=http.client.HTTPSConnection, context=None):
        if not isinstance(approval, guard.LiveApproval):
            raise guard.LiveModeDisabled("approval_required")
        self._approval = approval
        self._factory = connection_factory
        self._context = context if context is not None else make_tls_context()

    def __call__(self, request):
        guard.assert_request_safe(request)                           # approved URL, method, shape, bounds - before anything else
        self._approval.consume(request.method, request.url)         # the project is re-checked and the single use is spent BEFORE connecting
        parts = urlsplit(request.url)
        target = parts.path
        headers = {"Accept": "application/json", "Connection": "close"}
        if request.method == "POST":
            if request.params:                                       # the Web API key; the Phase 5 signBlob call has none and uses a bearer token
                target += "?" + urlencode({"key": request.params[0][1].reveal()})
            headers["Content-Type"] = request.content_type
            if request.bearer is not None:                           # the guard allows a bearer only on the signBlob request
                headers["Authorization"] = "Bearer " + request.bearer.reveal()
        connection = None
        try:
            connection = self._factory(parts.hostname, 443, timeout=request.connect_timeout, context=self._context)
            connection.connect()
            connection.sock.settimeout(request.read_timeout)
            connection.request(request.method, target, body=request.body if request.method == "POST" else None, headers=headers)
            response = connection.getresponse()
            status, chunks, size = response.status, [], 0
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                size += len(chunk)
                if size > request.max_response_bytes:
                    raise TransportFailure("too_large")
                chunks.append(chunk)
            return TransportResponse(status, b"".join(chunks), request.url)       # no redirect is ever followed, so the answering URL is the one asked
        except TransportFailure:
            raise
        except ssl.SSLError:                                          # includes certificate-verification failures
            raise TransportFailure("tls") from None
        except socket.gaierror:
            raise TransportFailure("dns") from None
        except (TimeoutError, socket.timeout):
            raise TransportFailure("timeout") from None
        except ConnectionRefusedError:
            raise TransportFailure("connect") from None
        except (ConnectionError, http.client.HTTPException):
            raise TransportFailure("reset") from None
        except OSError:
            raise TransportFailure("other") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
