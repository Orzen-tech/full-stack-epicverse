"""G4 interoperability GATE: does Dart pointycastle RSA-OAEP-SHA256 (MGF1-SHA256, label) decrypt in Python/OpenSSL?

The key pair exists only in memory: only the PUBLIC key (modulus and exponent) is given to Dart. Dummy random data only; nothing is
written to disk, printed or logged except counts. Exit code 0 only if every check passes.
Run (Windows):  python -B -I scripts/enc_auth_poc/g4/oaep_gate.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HERE = Path(__file__).resolve().parent
DART_PKG = HERE.parent / "g4_dart"
DART = r"D:\flutter sdk\flutter\bin\cache\dart-sdk\bin\dart.exe"
LABEL = b"epicverse-enc-auth-v1"
COUNT = 2000


def oaep(label):
    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=label)


def run_dart(pub, count, use_label):
    env = dict(os.environ, DART_SUPPRESS_ANALYTICS="true")
    cfg = {"n": format(pub.public_numbers().n, "x"), "e": pub.public_numbers().e, "label": LABEL.decode(), "count": count, "use_label": use_label}
    proc = subprocess.run([DART, "--packages=.dart_tool/package_config.json", "tool/oaep_gate.dart"], cwd=str(DART_PKG), env=env,
                          input=json.dumps(cfg) + "\n", capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise SystemExit("dart failed (exit %d): %s" % (proc.returncode, proc.stderr.strip()[:300]))
    return [line.split() for line in proc.stdout.splitlines() if line.strip()]


def main():
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    k = key.key_size // 8
    results = {}

    rows = run_dart(key.public_key(), COUNT, use_label=True)
    ok = bad = short = 0
    for cek_hex, ek_hex in rows:
        ek = bytes.fromhex(ek_hex)
        short += len(ek) != k
        try:
            ok += key.decrypt(ek, oaep(LABEL)) == bytes.fromhex(cek_hex)
        except ValueError:
            bad += 1
    results["dart->python with label: decrypted and equal"] = (ok, len(rows))
    results["dart->python with label: decrypt failures"] = (bad, 0)
    results["ek shorter than the modulus length"] = (short, 0)

    wrong = 0
    for cek_hex, ek_hex in rows[:50]:  # control 1: the label is really part of the encoding (wrong label must fail)
        try:
            key.decrypt(bytes.fromhex(ek_hex), oaep(b"some-other-label"))
        except ValueError:
            wrong += 1
    results["control: wrong label rejected by Python (of 50)"] = (wrong, 50)
    nolabel_rows = run_dart(key.public_key(), 50, use_label=False)  # control 2: Dart without a label must NOT open with the label
    rejected = 0
    for cek_hex, ek_hex in nolabel_rows:
        try:
            key.decrypt(bytes.fromhex(ek_hex), oaep(LABEL))
        except ValueError:
            rejected += 1
    results["control: Dart WITHOUT label rejected when Python expects the label (of 50)"] = (rejected, 50)
    plain = sum(1 for cek_hex, ek_hex in nolabel_rows if key.decrypt(bytes.fromhex(ek_hex), oaep(None)) == bytes.fromhex(cek_hex))
    results["control: Dart WITHOUT label opens with an empty label in Python (of 50)"] = (plain, 50)

    passed = True
    for name, (got, want) in results.items():
        good = got == want
        passed &= good
        print("%-84s %5d / %-5d %s" % (name, got, want, "ok" if good else "FAIL"))
    print("OAEP INTEROPERABILITY GATE:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
