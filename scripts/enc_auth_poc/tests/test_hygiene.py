"""Static checks that keep the PoC offline, secret-free and disconnected from Firebase and production code."""
import ast
import re
import sys
import unittest
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC))

# Built by concatenation so this file itself never contains the literal marker it searches for.
PRIVATE_KEY_MARKER = "PRIVATE" + " KEY-----"
MODULES = ["envelope.py", "nonces.py", "testkeys.py"]
ALLOWED_IMPORTS = {"base64", "hashlib", "json", "os", "re", "secrets", "struct", "threading", "time", "dataclasses",
                   "cryptography", "envelope"}
FORBIDDEN_CALLS = {"print", "open", "eval", "exec", "__import__", "getenv", "system", "popen"}


def files():
    # `.dart_tool` is generated (and git-ignored) by `dart pub get` inside the standalone g4_dart package
    return [p for p in POC.rglob("*") if p.is_file() and "__pycache__" not in p.parts and ".dart_tool" not in p.parts]


class Hygiene(unittest.TestCase):
    def test_no_private_key_material_in_any_file(self):
        for p in files():
            self.assertFalse(PRIVATE_KEY_MARKER in p.read_text(encoding="utf-8", errors="replace"), p.name)

    def test_modules_import_only_crypto_and_standard_library_pieces(self):
        for name in MODULES:
            tree = ast.parse((POC / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                for mod in mods:
                    self.assertTrue(mod.split(".")[0] in ALLOWED_IMPORTS, "%s imports %s" % (name, mod))

    def test_modules_never_print_read_files_or_environment(self):
        for name in MODULES:
            tree = ast.parse((POC / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    called = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                    self.assertFalse(called in FORBIDDEN_CALLS, "%s calls %s" % (name, called))
                if isinstance(node, ast.Attribute):
                    self.assertFalse(node.attr == "environ", name)

    def test_modules_do_not_reference_the_application_or_firebase(self):
        for name in MODULES:
            src = (POC / name).read_text(encoding="utf-8").lower()
            for word in ("firebase_admin", "identitytoolkit", "googleapis", "asyncpg", "cloud run"):
                self.assertFalse(word in src, "%s mentions %s" % (name, word))
            self.assertFalse(re.search(r"^\s*(from|import)\s+(app|backend|frontend)\b", src, re.M), name)

    def test_poc_contains_only_the_expected_kinds_of_files(self):
        allowed = {".py", ".md", ".gitattributes"}
        dart_package_allowed = {".dart", ".yaml", ".lock", ".gitignore"}  # only inside the standalone g4_dart package
        g6a_allowed = {".patch", ".sh", ".txt", ".dart"}                   # only inside g6a: the patch, build script, manifest, Dart probe
        g6b_allowed = {".patch", ".sh"}                                    # only inside g6b: the patch and the hash-gated build script
        g7_allowed = {".patch", ".sh"}                                     # only inside g7: the real-Firebase adapter patch and its build script
        for p in files():
            parts = p.relative_to(POC).parts
            self.assertTrue(p.suffix in allowed or p.name in allowed
                            or ("g4_dart" in parts and (p.suffix in dart_package_allowed or p.name in dart_package_allowed))
                            or ("g6a" in parts and p.suffix in g6a_allowed)
                            or ("g6b" in parts and p.suffix in g6b_allowed)
                            or ("g7" in parts and p.suffix in g7_allowed), p.name)


if __name__ == "__main__":
    unittest.main()
