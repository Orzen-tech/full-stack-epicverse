"""Project guard tests (offline). A: correct project accepted. B/C: others and missing rejected. D/I: environment conflicts and
production-like configuration rejected. E/F: hosts and HTTP rejected. J/K: missing and malformed API key rejected."""
import socket
import unittest

import _support as S
import g3_guard as guard
from g3_guard import GuardError, Secret

GOOD_KEY = S.synthetic_api_key()


class ProjectIsolation(unittest.TestCase):
    def test_A_the_test_project_is_accepted(self):
        self.assertEqual(guard.validate_project("epicverse-g3-auth-test"), "epicverse-g3-auth-test")
        config = guard.load_config("epicverse-g3-auth-test", GOOD_KEY, env={})
        self.assertEqual(config.project_id, S.ALLOWED)

    def test_B_every_other_project_is_rejected(self):
        for other in ("epicverse", "epicverse-prod", "epicverse-production", "epicverse-1", "epicverse-g3-auth-tes", "epicverse-g3-auth-test2",
                      "epicverse-g3-auth-test-prod", "x-epicverse-g3-auth-test", "EPICVERSE-G3-AUTH-TEST", "Epicverse-G3-Auth-Test",
                      " epicverse-g3-auth-test", "epicverse-g3-auth-test ", "epicverse-g3-auth-test\n", "epicverse-g3-auth-test/",
                      "epicverse-g3-auth-test.", "projects/epicverse-g3-auth-test", "epicverse-g3-auth-te\u0455t", "epicverse\u2011g3-auth-test",
                      "demo-epicverse-g3", "my-firebase-app-12345", "firebase-adminsdk", "g3", "*", "epicverse-g3-auth-test,epicverse-prod"):
            with self.assertRaises(GuardError, msg=repr(other)) as raised:
                guard.validate_project(other)
            self.assertIn(raised.exception.reason, {"project_not_allowed"})

    def test_C_a_missing_project_is_rejected(self):
        for missing in (None, ""):
            with self.assertRaises(GuardError) as raised:
                guard.load_config(missing, GOOD_KEY, env={})
            self.assertEqual(raised.exception.reason, "project_missing")
        for wrong_type in (123, b"epicverse-g3-auth-test", ["epicverse-g3-auth-test"], True):
            with self.assertRaises(GuardError):
                guard.validate_project(wrong_type)

    def test_the_project_is_never_discovered_from_the_environment(self):
        env = {"GOOGLE_CLOUD_PROJECT": S.ALLOWED, "GCLOUD_PROJECT": S.ALLOWED, "FIREBASE_PROJECT_ID": S.ALLOWED}
        with self.assertRaises(GuardError) as raised:
            guard.load_config(None, GOOD_KEY, env=env)
        self.assertEqual(raised.exception.reason, "project_missing")

    def test_the_project_is_checked_before_the_environment_and_the_key(self):
        with self.assertRaises(GuardError) as raised:
            guard.load_config("epicverse-prod", "not-a-key", env={"FIREBASE_TOKEN": "x"})
        self.assertEqual(raised.exception.reason, "project_not_allowed")
        with self.assertRaises(GuardError) as raised:
            guard.load_config(S.ALLOWED, "not-a-key", env={"FIREBASE_TOKEN": "x"})
        self.assertEqual(raised.exception.reason, "environment_forbidden")


class Environment(unittest.TestCase):
    def test_D_conflicting_project_variables_are_rejected(self):
        for name in guard.PROJECT_ENV_VARS:
            with self.assertRaises(GuardError, msg=name) as raised:
                guard.validate_environment({name: "some-other-project"})
            self.assertEqual((raised.exception.reason, raised.exception.detail), ("environment_conflict", name))

    def test_project_variables_naming_the_allowed_project_or_empty_are_tolerated(self):
        guard.validate_environment({"GOOGLE_CLOUD_PROJECT": S.ALLOWED, "GCLOUD_PROJECT": "", "FIREBASE_PROJECT_ID": "  "})

    def test_I_credentials_emulators_and_firebase_configuration_are_refused_whenever_set(self):
        for name in guard.FORBIDDEN_ENV_VARS:
            with self.assertRaises(GuardError, msg=name) as raised:
                guard.validate_environment({name: "anything"})
            self.assertEqual((raised.exception.reason, raised.exception.detail), ("environment_forbidden", name))

    def test_I_production_like_firebase_configuration_is_rejected(self):
        for env in ({"FIREBASE_CONFIG": '{"projectId":"epicverse-prod","apiKey":"x"}'},
                    {"FIREBASE_CONFIG": '{"projectId":"%s"}' % S.ALLOWED},                      # even the right project: no hidden configuration
                    {"GOOGLE_APPLICATION_CREDENTIALS": "C:/keys/prod-service-account.json"},
                    {"FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"},
                    {"GOOGLE_CLOUD_PROJECT": "epicverse-prod"}):
            with self.assertRaises(GuardError):
                guard.load_config(S.ALLOWED, GOOD_KEY, env=env)

    def test_the_error_names_the_variable_but_never_its_value(self):
        secret_value = "super-secret-" + S.synthetic_password()
        with self.assertRaises(GuardError) as raised:
            guard.validate_environment({"GOOGLE_APPLICATION_CREDENTIALS": secret_value})
        self.assertNotIn(secret_value, str(raised.exception))
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", str(raised.exception))

    def test_the_process_environment_is_used_only_when_no_mapping_is_given(self):
        import os
        old = os.environ.get("FIREBASE_TOKEN")
        os.environ["FIREBASE_TOKEN"] = "x"
        try:
            with self.assertRaises(GuardError):
                guard.validate_environment()
            guard.validate_environment({})                      # an explicit mapping wins
        finally:
            if old is None:
                del os.environ["FIREBASE_TOKEN"]
            else:
                os.environ["FIREBASE_TOKEN"] = old


class ApiKey(unittest.TestCase):
    def test_J_a_missing_key_is_rejected(self):
        for missing in (None, "", Secret("")):
            with self.assertRaises(GuardError) as raised:
                guard.validate_api_key(missing)
            self.assertEqual(raised.exception.reason, "api_key_missing")

    def test_K_malformed_keys_are_rejected(self):
        good = S.synthetic_api_key()
        for bad in (good[:-1], good + "A", good.lower().replace("aiza", "bbbb"), "AIza" + "!" * 35, "AIza" + " " * 35, good + "\n", " " + good,
                    good.replace("AIza", "AIzb", 1), "x" * 39, "Bearer " + good, 12345, b"AIza" + b"A" * 35, "AIza" + "\u00e9" * 35,
                    "ya29." + "A" * 100, "-----" + "A" * 40):
            with self.assertRaises(GuardError, msg=repr(type(bad))):
                guard.validate_api_key(bad)

    def test_a_good_key_is_wrapped_and_never_printed(self):
        key = S.synthetic_api_key()
        wrapped = guard.validate_api_key(key)
        for text in (repr(wrapped), str(wrapped), "%s" % wrapped, "{}".format(wrapped), f"{wrapped}", repr(guard.load_config(S.ALLOWED, key, env={}))):
            self.assertNotIn(key, text)


class Urls(unittest.TestCase):
    OK1 = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
    OK2 = "https://securetoken.googleapis.com/v1/token"

    def test_approved_endpoints_pass(self):
        self.assertEqual(guard.validate_url(self.OK1), self.OK1)
        self.assertEqual(guard.validate_url(self.OK2), self.OK2)

    def test_E_unexpected_hosts_are_rejected(self):
        for url in ("https://evil.example/v1/token", "https://identitytoolkit.googleapis.com.evil.example/v1/accounts:signInWithPassword",
                    "https://evil.example/identitytoolkit.googleapis.com/v1/token", "https://identitytoolkit.googleapis.com@evil.example/v1/token",
                    "https://user:pw@identitytoolkit.googleapis.com/v1/accounts:signInWithPassword", "https://www.googleapis.com/v1/token",
                    "https://firebase.googleapis.com/v1/token", "https://127.0.0.1/v1/token", "https://localhost/v1/token", "https://[::1]/v1/token",
                    "https://identitytoolkit.googleapis.com:8443/v1/accounts:signInWithPassword", "https://identitytoolkit.googleapis.com:443/v1/token",
                    "https://IDENTITYTOOLKIT.googleapis.com/v1/accounts:signInWithPassword", "https://identitytoolkit.googleapis.com\\@evil.example/",
                    "//identitytoolkit.googleapis.com/v1/token", "identitytoolkit.googleapis.com/v1/token", "", None, 5):
            with self.assertRaises(GuardError, msg=repr(url)):
                guard.validate_url(url)

    def test_F_http_and_other_schemes_are_rejected(self):
        for url in ("http://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword", "http://securetoken.googleapis.com/v1/token",
                    "ftp://securetoken.googleapis.com/v1/token", "ws://securetoken.googleapis.com/v1/token", "file:///etc/passwd",
                    "HTTP://securetoken.googleapis.com/v1/token"):
            with self.assertRaises(GuardError, msg=url) as raised:
                guard.validate_url(url)
            self.assertIn(raised.exception.reason, {"url_not_https", "url_invalid"})

    def test_unexpected_paths_queries_and_fragments_are_rejected(self):
        for url in (self.OK1 + "?key=abc", self.OK2 + "#x", "https://identitytoolkit.googleapis.com/v1/accounts:delete",
                    "https://identitytoolkit.googleapis.com/v1/accounts:signUp", "https://identitytoolkit.googleapis.com/v1/token",
                    "https://securetoken.googleapis.com/v1/token/", "https://securetoken.googleapis.com/v1/../v1/token",
                    "https://securetoken.googleapis.com/v1/token%2f", self.OK2 + " ", self.OK2 + "\n", "https://securetoken.googleapis.com/v1/tok\u00e9n"):
            with self.assertRaises(GuardError, msg=repr(url)):
                guard.validate_url(url)


class FakeRequest:
    def __init__(self, **overrides):
        self.method, self.url = "POST", Urls.OK1
        self.params, self.body, self.content_type = (("key", Secret(GOOD_KEY)),), b"{}", "application/json"
        self.connect_timeout, self.read_timeout, self.max_response_bytes = 5.0, 10.0, 65536
        self.follow_redirects, self.trust_env = False, False
        self.__dict__.update(overrides)


class RequestSafety(unittest.TestCase):
    def test_a_safe_request_passes(self):
        guard.assert_request_safe(FakeRequest())

    def test_G_redirects_are_refused(self):
        with self.assertRaises(GuardError) as raised:
            guard.assert_request_safe(FakeRequest(follow_redirects=True))
        self.assertEqual(raised.exception.reason, "request_redirects")

    def test_H_environment_proxies_are_refused(self):
        with self.assertRaises(GuardError) as raised:
            guard.assert_request_safe(FakeRequest(trust_env=True))
        self.assertEqual(raised.exception.reason, "request_proxy_env")

    def test_every_other_unsafe_property_is_refused(self):
        for overrides in ({"method": "GET"}, {"url": "http://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"},
                          {"url": "https://evil.example/"}, {"connect_timeout": 0}, {"connect_timeout": None}, {"read_timeout": 31},
                          {"read_timeout": True}, {"max_response_bytes": 10 ** 9}, {"max_response_bytes": 0}, {"content_type": "text/html"},
                          {"params": ()}, {"params": (("key", Secret("bad")),)}, {"params": (("other", Secret(GOOD_KEY)),)},
                          {"params": (("key", Secret(GOOD_KEY)), ("x", "y"))}, {"body": "text"}):
            with self.assertRaises(GuardError, msg=str(overrides)[:60]):
                guard.assert_request_safe(FakeRequest(**overrides))


class LiveMode(unittest.TestCase):
    def test_live_mode_is_off_by_default_and_refuses(self):
        self.assertIs(guard.LIVE_MODE_ENABLED, False)
        with self.assertRaises(guard.LiveModeDisabled):
            guard.require_live_mode()


class SecretWrapper(unittest.TestCase):
    def test_secret_is_never_rendered_pickled_or_hashed(self):
        import pickle
        value = S.synthetic_password()
        secret = Secret(value)
        for text in (repr(secret), str(secret), "%s" % secret, "%r" % secret, "{:>20}".format(secret), f"{secret!s}", repr([secret]), repr({"k": secret})):
            self.assertNotIn(value, text)
        self.assertEqual(secret.reveal(), value)
        with self.assertRaises(TypeError):
            pickle.dumps(secret)
        with self.assertRaises(TypeError):
            hash(secret)
        with self.assertRaises(AttributeError):
            secret._value = "x"


class NetworkDenialSelfCheck(unittest.TestCase):
    def test_T_the_denial_layer_blocks_name_resolution_and_connections_without_touching_the_network(self):
        before = len(S.ATTEMPTS)
        with self.assertRaises(S.NetworkDenied):
            socket.getaddrinfo("name.invalid", 443)           # patched: raises immediately, nothing is resolved or sent
        with self.assertRaises(S.NetworkDenied):
            socket.create_connection(("name.invalid", 443))
        sock = socket.socket()
        try:
            with self.assertRaises(S.NetworkDenied):
                sock.connect(("name.invalid", 443))
        finally:
            sock.close()
        self.assertEqual(len(S.ATTEMPTS) - before, 3)
        del S.ATTEMPTS[before:]


if __name__ == "__main__":
    unittest.main()
