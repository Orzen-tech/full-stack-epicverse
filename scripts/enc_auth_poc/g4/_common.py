"""Shared helpers for the G4 tests: locating Dart and running the Dart sealer tool on a batch of requests."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POC = HERE.parent
DART_PKG = POC / "g4_dart"
sys.path.insert(0, str(POC))

_FLUTTER_DART = Path(r"D:\flutter sdk\flutter\bin\cache\dart-sdk\bin\dart.exe")
DART = str(_FLUTTER_DART) if _FLUTTER_DART.exists() else shutil.which("dart")


def dart_env():
    return dict(os.environ, DART_SUPPRESS_ANALYTICS="true")


def run_seal_cli(requests):
    """Sends one JSON line per request to tool/seal_cli.dart; returns (list of result dicts, stderr text)."""
    data = ("\n".join(json.dumps(r) for r in requests) + "\n").encode("utf-8")
    proc = subprocess.run([DART, "--packages=.dart_tool/package_config.json", "tool/seal_cli.dart"], cwd=str(DART_PKG), env=dart_env(),
                          input=data, capture_output=True, timeout=900)
    out = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines() if line.strip()]
    return out, proc.stderr.decode("utf-8", "replace"), proc.returncode
