"""Developer tool: writes the current SHA-256 of enc_auth_gateway_g7_firebase.patch (CR-stripped) into build_tree_enc_auth_g7.sh."""
import hashlib
import re
from pathlib import Path

G7 = Path(__file__).resolve().parent.parent
digest = hashlib.sha256(G7.joinpath("enc_auth_gateway_g7_firebase.patch").read_bytes().replace(b"\r", b"")).hexdigest()
script = G7 / "build_tree_enc_auth_g7.sh"
text = script.read_text(encoding="utf-8")
new, n = re.subn(r"^G7_PATCH_SHA256=.*$", "G7_PATCH_SHA256=" + digest, text, flags=re.M)
assert n == 1
script.write_text(new, encoding="utf-8", newline="\n")
print("pinned", digest)
