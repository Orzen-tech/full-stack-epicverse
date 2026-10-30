"""The ONE list of repository changes that the explicitly approved LOCAL encrypted-auth integration (Flutter app + backend gateway) is allowed to make.

The historical isolation tests (G3, G6a, G6b static checks) used to require "no tracked file changed, nothing outside the PoC folder". They
now accept exactly the files below and still FAIL for anything else: any other tracked file, any backend file, anything staged, a delete or
rename, an untracked file outside the approved folders, or a path trying to climb out of them.

Standard library only; read-only (asks Git for status, changes nothing).
"""
import hashlib
import subprocess
from pathlib import Path

# Tracked files the approved integration modifies (exact paths, status " M" = modified in the working tree, not staged).
APPROVED_TRACKED_CHANGES = frozenset({
    "frontend/EpicVerseApp/lib/core/security/password_reauth.dart",
    "frontend/EpicVerseApp/lib/presentation/screens/change_password_screen.dart",
    "frontend/EpicVerseApp/lib/presentation/screens/create_profile_screen.dart",
    "frontend/EpicVerseApp/lib/presentation/screens/login_screen.dart",
    "frontend/EpicVerseApp/pubspec.lock",
    "frontend/EpicVerseApp/pubspec.yaml",
})

# Folders in which NEW (untracked) files are expected: the proof-of-concept folder and the three folders the integration added.
APPROVED_NEW_PREFIXES = (
    "scripts/enc_auth_poc/",
    "frontend/EpicVerseApp/lib/core/security/enc_auth/",
    "frontend/EpicVerseApp/test/enc_auth/",
    "frontend/EpicVerseApp/tool/enc_auth_local/",
)


# The encrypted-auth gateway as integrated into the local backend (separately approved; HEAD-based port verified in an isolated copy first).
# EXACT paths only - never a folder prefix - so every other backend change is still reported: 4 modified tracked files and 10 new files.
APPROVED_BACKEND_MODIFIED = frozenset({
    "backend/app/api/routes.py",
    "backend/app/core/config.py",
    "backend/app/services/user_db.py",
    "backend/scripts/requirements.txt",
})
APPROVED_BACKEND_NEW = frozenset({
    "backend/app/api/enc_gateway.py",
    "backend/app/services/enc_auth_diag.py",
    "backend/app/services/enc_auth_envelope.py",
    "backend/app/services/enc_auth_identity.py",
    "backend/app/services/enc_auth_identity_firebase.py",
    "backend/app/services/enc_auth_limits_eval.py",
    "backend/app/services/enc_auth_policy.py",
    "backend/app/services/enc_auth_retry.py",
    "backend/app/services/enc_auth_store.py",
    "backend/tests/test_enc_auth_port.py",
})
APPROVED_BACKEND = APPROVED_BACKEND_MODIFIED | APPROVED_BACKEND_NEW

# The verification document (documentation only, no code). EXACT path, never a folder prefix.
APPROVED_DOCS_NEW = frozenset({
    "docs/epicverse-encrypted-auth-verification.md",
})

# SHA-256 (carriage returns removed) of the 14 approved files as verified. A path being approved does not approve ANY edit of it: a later change to one
# of these files is reported by backend_content_problems() until the pin is deliberately updated together with a new review.
APPROVED_BACKEND_SHA256 = {
    "backend/app/api/routes.py": "e6416deffc713ff6567d04e3e36b28ea6f7f337de6106d717a8f9b0591c48ad8",
    "backend/app/core/config.py": "7d675728b10711f7de6f25180890a58ce503d40575ec1cc447fb4c08f6e0f3e4",
    "backend/app/services/user_db.py": "8260c04f9b454251b0f4f5b8ab7f003cae9189724a8bcb7900f5b52f161a8e25",
    "backend/scripts/requirements.txt": "8d27c534aad39d994637f2a9f3af605c2061513b98c1c8ac65bc05d5629a9bad",
    "backend/app/api/enc_gateway.py": "4f3a4ed68a49458ababe031e996658694d4e1cbcc32d042268be67e8527521c6",
    "backend/app/services/enc_auth_diag.py": "e17fcef33107df37c20d0819c45e41f89381398a248225fa1fe25b1676382ab8",
    "backend/app/services/enc_auth_envelope.py": "f8b1d4656d2322245075451546486a6acfc873312b6e621fde8d5a1c78b77764",
    "backend/app/services/enc_auth_identity.py": "05ca38e01161a57a1a322b6021b4280577279046d709b8ccb5536e4e92a1d7fb",
    "backend/app/services/enc_auth_identity_firebase.py": "77b21d1ec7a03fdc086024664f6c1c8ccc3531efddb0492b895aa23101f204cf",
    "backend/app/services/enc_auth_limits_eval.py": "77d0e193a4b0c7c57d0e083f4e1998da446ef2ed20c4299bfee70c65e6433bb9",
    "backend/app/services/enc_auth_policy.py": "a0f9ebda0110855a5ba52db1cde6c560fa97441db84828531d706168f5ea229d",
    "backend/app/services/enc_auth_retry.py": "365dceaeedb667694c9f07f438e189eb3743233c71f0567318bfe243df5259b2",
    "backend/app/services/enc_auth_store.py": "15bc44dada501faaacb516b03b1997617536832f6f0b4d850e77fc414b97ec7b",
    "backend/tests/test_enc_auth_port.py": "92352721b2b17a9d689f9391ab7780bebce9d88ffad2e97d173d197143bd4dfa",
}
assert set(APPROVED_BACKEND_SHA256) == APPROVED_BACKEND


def backend_content_problems(repo):
    """Approved backend paths whose content is missing or differs from the verified pin (empty list = all 14 are exactly as verified)."""
    bad = []
    for rel in sorted(APPROVED_BACKEND):
        path = Path(repo) / rel
        if not path.is_file() or hashlib.sha256(path.read_bytes().replace(b"\r", b"")).hexdigest() != APPROVED_BACKEND_SHA256[rel]:
            bad.append(rel)
    return bad


def porcelain(repo, *paths):
    """`git status --porcelain --untracked-files=all` (not stripped: the first column matters)."""
    command = ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"]
    if paths:
        command += ["--", *paths]
    return subprocess.run(command, capture_output=True, text=True).stdout


def unapproved(porcelain_text):
    """The status lines that are NOT part of the approved integration (an empty list means everything is approved)."""
    bad = []
    for line in porcelain_text.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].strip()
        if ".." in path.split("/") or path.startswith(("/", "\\")):
            bad.append(line)
        elif status == " M" and (path in APPROVED_TRACKED_CHANGES or path in APPROVED_BACKEND_MODIFIED):
            continue
        elif status == "??" and (path.startswith(APPROVED_NEW_PREFIXES) or path in APPROVED_BACKEND_NEW or path in APPROVED_DOCS_NEW):
            continue
        else:
            bad.append(line)
    return bad
