"""Developer tool: writes the current SHA-256 of enc_auth_gateway.patch (CR-stripped) into build_tree_enc_auth.sh."""
import hashlib
import re
from pathlib import Path

G6A = Path(__file__).resolve().parent.parent
digest = hashlib.sha256(G6A.joinpath("enc_auth_gateway.patch").read_bytes().replace(b"\r", b"")).hexdigest()
script = G6A / "build_tree_enc_auth.sh"
text = script.read_text(encoding="utf-8")
new, n = re.subn(r"^ENC_AUTH_PATCH_SHA256=.*$", "ENC_AUTH_PATCH_SHA256=" + digest, text, flags=re.M)
assert n == 1
script.write_text(new, encoding="utf-8", newline="\n")
print("pinned", digest)
