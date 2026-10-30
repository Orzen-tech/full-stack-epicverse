"""G3 Phase 5 (PREPARATION ONLY): a custom-token signer that asks Google to sign through the IAM Credentials API `signBlob` - the private key never
leaves Google and nothing is downloaded. It uses an injected transport and the guard's exact allowlist (one URL per service account of the test
project); the caller credential is a short-lived bearer access token passed in as a Secret. Nothing here runs live: the guard grants the needed
approval only through grant_custom_token_approval(), which refuses until this phase is separately approved.

Failures raise SignerError with a fixed category (never Google's message, the token, the payload or the signature). One attempt, no retry.
"""
import base64
import binascii
import json
import re

import g3_custom_token as ct
import g3_guard as guard
import g3_rest_client as rc
from g3_guard import Secret

CATEGORIES = frozenset({
    "transport", "redirect", "bad_response", "unauthenticated", "permission_denied", "service_disabled", "not_found", "invalid_argument",
    "failed_precondition", "rate_limited", "unavailable", "unexpected",
})
_STATUS_CATEGORY = {"UNAUTHENTICATED": "unauthenticated", "PERMISSION_DENIED": "permission_denied", "NOT_FOUND": "not_found",
                    "INVALID_ARGUMENT": "invalid_argument", "FAILED_PRECONDITION": "failed_precondition", "RESOURCE_EXHAUSTED": "rate_limited",
                    "UNAVAILABLE": "unavailable"}
_HTTP_CATEGORY = {401: "unauthenticated", 403: "permission_denied", 404: "not_found", 400: "invalid_argument", 429: "rate_limited"}
_KEY_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,128}")


class SignerError(Exception):
    def __init__(self, category):
        super().__init__("signing failed: %s" % category)
        self.category = category if category in CATEGORIES else "unexpected"


def _category(status, document):
    """A fixed category from the HTTP status, Google's `error.status` and the `reason` of any error detail - free text is never used."""
    error = document.get("error") if isinstance(document, dict) else None
    if isinstance(error, dict):
        for detail in error.get("details") or []:
            if isinstance(detail, dict) and detail.get("reason") == "SERVICE_DISABLED":
                return "service_disabled"            # the IAM Credentials API is not enabled on the project
        named = _STATUS_CATEGORY.get(error.get("status"))
        if named:
            return named
    return _HTTP_CATEGORY.get(status, "unavailable" if status >= 500 else "unexpected")


class IamSignBlobSigner(ct.Signer):
    def __init__(self, service_account, access_token, transport):
        self._url = guard.sign_blob_url(service_account)           # only a service account of the test project
        try:
            guard.validate_bearer_token(access_token)
        except guard.GuardError:
            raise SignerError("unexpected") from None
        if not callable(transport):
            raise guard.GuardError("transport_invalid")
        self._token, self._transport = access_token, transport
        self.key_id_returned = False

    def sign(self, signing_input):
        if not isinstance(signing_input, bytes) or not 20 <= len(signing_input) <= 4096 or signing_input.count(b".") != 1:
            raise SignerError("invalid_argument")
        body = json.dumps({"payload": base64.b64encode(signing_input).decode("ascii")}, separators=(",", ":")).encode("ascii")
        request = rc.TransportRequest("POST", self._url, (), body, "application/json", guard.CONNECT_TIMEOUT_SECONDS, guard.READ_TIMEOUT_SECONDS,
                                      guard.MAX_RESPONSE_BYTES, bearer=self._token)
        guard.assert_request_safe(request)
        try:
            response = self._transport(request)                      # exactly one attempt
        except rc.TransportFailure:
            raise SignerError("transport") from None
        except guard.LiveModeDisabled:
            raise
        except Exception:
            raise SignerError("transport") from None
        rejected, screened = rc.screen_response("sign_blob", response, self._url)
        if rejected is not None:
            raise SignerError("redirect" if rejected.outcome == rc.REDIRECT_REJECTED else "bad_response")
        document, _ = screened
        if response.status != 200:
            raise SignerError(_category(response.status, document))
        blob, key_id = document.get("signedBlob"), document.get("keyId")
        if not isinstance(blob, str) or not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
            raise SignerError("bad_response")
        try:
            signature = base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError):
            raise SignerError("bad_response") from None
        if not 128 <= len(signature) <= 1024:
            raise SignerError("bad_response")
        self.key_id_returned = True
        return signature
