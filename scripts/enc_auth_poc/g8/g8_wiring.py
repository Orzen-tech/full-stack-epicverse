"""G8 wiring: builds the real ports (G7 adapter, Admin SDK with a bearer credential, G3 session client) around ONE `Egress`. Nothing here runs by
itself and nothing here is reachable while live execution is disabled; construction makes no network call and reads no credential from the
machine: no Application Default Credentials, no key file, no gcloud. Short-lived access tokens are handed in explicitly as Secrets.
"""
import asyncio
import hashlib
import sys
import time
from pathlib import Path

import g3_guard as g3
from g8_guard import ADMIN_HTTP_TIMEOUT_SECONDS, ALLOWED_PROJECT_ID, LiveSafetyStop, g3_transport, guarded_client_factory
from g8_stages import Claims, SessionOutcome

# The adapter files this harness was written against (CR-stripped SHA-256 of the files in the hash-gated G7 build). Any other bytes: no run.
ADAPTER_PINS = {
    "app/services/enc_auth_identity_firebase.py": "77b21d1ec7a03fdc086024664f6c1c8ccc3531efddb0492b895aa23101f204cf",
    "app/services/enc_auth_identity.py": "05ca38e01161a57a1a322b6021b4280577279046d709b8ccb5536e4e92a1d7fb",
}


def verify_tree(tree_dir):
    """Hash-checks the built G7 tree and makes its backend importable. Returns (adapter_module, settings)."""
    backend = Path(tree_dir) / "backend"
    for relative, expected in ADAPTER_PINS.items():
        path = backend / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes().replace(b"\r", b"")).hexdigest() != expected:
            raise LiveSafetyStop("adapter_hash_mismatch")
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.core.config import settings
    from app.services import enc_auth_identity_firebase as real
    return real, settings


def disable_retries(settings):
    settings.ENC_AUTH_RETRY_MAX = 0


def delete_app(app):
    try:
        import firebase_admin
        firebase_admin.delete_app(app)
    except Exception:
        pass


def configure_settings(settings, signer_account, api_key):
    """No automatic retries (the cap is counted per attempt anyway) and the one allowed project. Settings are plain attributes here."""
    g3.validate_service_account(signer_account)
    disable_retries(settings)
    settings.ENC_AUTH_FIREBASE_PROJECT_ID = settings.FIREBASE_PROJECT_ID = ALLOWED_PROJECT_ID
    settings.ENC_AUTH_SIGNER_SERVICE_ACCOUNT = signer_account
    settings.ENC_AUTH_FIREBASE_WEB_API_KEY = api_key.reveal()
    settings.GOOGLE_APPLICATION_CREDENTIALS = ""


def static_token_provider(real, token):
    class StaticTokenProvider(real.AccessTokenProvider):
        async def access_token(self):
            return token.reveal()
    return StaticTokenProvider()


def build_admin_app(name, access_token):
    """A firebase_admin app that authenticates with the pasted short-lived token only (no ADC, no key file, no refresh)."""
    import firebase_admin
    from firebase_admin import credentials
    from google.oauth2 import credentials as oauth

    class BearerCredential(credentials.Base):
        def get_credential(self):
            return oauth.Credentials(token=access_token.reveal())

    return firebase_admin.initialize_app(BearerCredential(), options={"projectId": ALLOWED_PROJECT_ID, "httpTimeout": ADMIN_HTTP_TIMEOUT_SECONDS}, name=name)


def build_identity(real, egress, api_key, signer_account, signer_token, admin_app):
    factory = guarded_client_factory(egress, ADMIN_HTTP_TIMEOUT_SECONDS)
    return real.FirebaseIdentity(real.HttpxIdentityToolkit(api_key.reveal(), factory),
                                 real.FirebaseAdminPort(ALLOWED_PROJECT_ID, ADMIN_HTTP_TIMEOUT_SECONDS, app=admin_app),
                                 real.IamSignBlobPort(signer_account, static_token_provider(real, signer_token), factory), signer_account)


class OpsUnavailable(Exception):
    """A disable / enable / delete / lookup failed; the outcome of a write may be unknown. No library text is attached."""


class FirebaseAccountOps:
    """disable / enable / delete / find-by-e-mail through the Admin SDK. Wrapped by GuardedAccountOps; never used directly by a stage."""

    def __init__(self, app, auth_module=None, exceptions_module=None):
        self._app = app
        if auth_module is None:
            from firebase_admin import auth as auth_module
        self._auth = auth_module

    async def _run(self, function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except Exception:
            raise OpsUnavailable() from None

    async def set_disabled(self, uid, flag):
        await self._run(self._auth.update_user, uid, disabled=bool(flag), app=self._app)

    async def delete_user(self, uid):
        await self._run(self._auth.delete_user, uid, app=self._app)

    async def find_uid(self, email):
        try:
            user = await asyncio.to_thread(self._auth.get_user_by_email, email, app=self._app)
        except getattr(self._auth, "UserNotFoundError", ()):
            return None
        except Exception:
            raise OpsUnavailable() from None
        return user.uid


class G3Session:
    """Async facade over G3's REST client (refresh, custom-token exchange, certificates) and G3's RS256 verifier. The client's transport is the
    G8 one, so every call is charged to the same budget."""

    def __init__(self, client, clock=time.time):
        self._client, self._clock, self._verifier = client, clock, None

    @staticmethod
    def _outcome(result):
        tokens = result.tokens
        return SessionOutcome(ok=result.outcome == "ok" and tokens is not None, code=result.error_code if result.outcome != "ok" else "-",
                              id_token=tokens.id_token if tokens else None, refresh_token=tokens.refresh_token if tokens else None,
                              is_new_user=tokens.is_new_user if tokens else None)

    async def load_certs(self):
        from g3_verify import FirebaseSignatureVerifier
        result = await asyncio.to_thread(self._client.fetch_public_certificates)
        if result.outcome != "ok" or not result.data:
            raise LiveSafetyStop("certificates_unavailable")
        self._verifier = FirebaseSignatureVerifier(result.data)

    async def sign_in(self, email, password):
        return self._outcome(await asyncio.to_thread(self._client.sign_in, email, password))

    async def refresh(self, refresh_token):
        return self._outcome(await asyncio.to_thread(self._client.refresh, refresh_token))

    async def exchange(self, custom_token):
        return self._outcome(await asyncio.to_thread(self._client.sign_in_with_custom_token, custom_token))

    async def claims(self, id_token):
        import g3_token_inspect as ti
        if self._verifier is None:
            raise LiveSafetyStop("certificates_not_loaded")
        try:
            verified = self._verifier.verify(id_token, ALLOWED_PROJECT_ID, int(self._clock()))
        except ti.TokenInspectionError:
            return None
        c = verified.claims
        return Claims(c.uid, c.issued_at, c.sign_in_provider if isinstance(c.sign_in_provider, str) else "other")
