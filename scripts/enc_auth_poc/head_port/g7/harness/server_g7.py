"""In-container runner of the G7 lab: the built (hash-gated) backend with the REAL-Firebase adapter selected and wired to a FAKE Firebase world
(no network, no Google service). TEST HARNESS ONLY. It reuses the G6b helper routes (outbox, issue_proof, faults) and adds /__g6b/g7/* controls.
Control routes sit under /__g6b so the existing test client addresses them without the /api/v1 prefix.
"""
import os
import sys

sys.path.insert(0, os.environ.get("PYTHONPATH", "/src/backend").split(":")[0])
sys.path.insert(0, "/h")      # the G6b harness (server_g6b.py)
sys.path.insert(0, "/g7")     # this folder (fake_firebase.py)

import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import server_g6b  # noqa: E402,F401  (defines the shared helper routes and wraps the identity operations with fault switches)
import fake_firebase as ff  # noqa: E402
from app.main import app  # noqa: E402
import app.services.enc_auth_identity_firebase as real  # noqa: E402

WORLD = ff.World(project=os.environ["FIREBASE_PROJECT_ID"])
real.configure(ff.build_identity(WORLD))


async def body(request):
    return await request.json()


@app.post("/__g6b/g7/account")
async def add_account(request: Request):
    data = await body(request)
    return JSONResponse({"uid": WORLD.add_account(data["email"], data["password"], bool(data.get("disabled")))})


@app.post("/__g6b/g7/id_token")
async def id_token(request: Request):
    data = await body(request)
    return JSONResponse({"token": WORLD.id_token_for(data["uid"], data.get("ttl", 3600), None if "issued_ago" not in data else WORLD.clock() - data["issued_ago"])})


@app.post("/__g6b/g7/disable")
async def disable(request: Request):
    data = await body(request)
    WORLD.accounts[data["uid"]]["disabled"] = bool(data.get("disabled", True))
    return JSONResponse({"ok": True})


@app.post("/__g6b/g7/revoke")
async def revoke(request: Request):
    data = await body(request)
    WORLD.accounts[data["uid"]]["valid_since"] = int(WORLD.clock())
    return JSONResponse({"ok": True})


@app.post("/__g6b/g7/fault")
async def fault(request: Request):
    data = await body(request)
    if data.get("clear"):
        WORLD.clear_faults()
    else:
        WORLD.fault(data["operation"], data.get("kind", "unavailable"), data.get("cause", "http_503"), data.get("times", 1), bool(data.get("throttle")))
    return JSONResponse({"ok": True})


@app.post("/__g6b/g7/reset")
async def reset(request: Request):
    WORLD.reset_counters()
    return JSONResponse({"ok": True})


@app.post("/__g6b/g7/policy")
async def policy(request: Request):
    WORLD.min_password_length = int((await body(request))["min_length"])
    return JSONResponse({"ok": True})


@app.post("/__g6b/g7/state")
async def state(request: Request):
    data = await body(request)
    out = {"calls": dict(WORLD.calls), "events": list(WORLD.events), "account_count": len(WORLD.accounts)}
    uid = data.get("uid") or WORLD.find_by_email(data.get("email", ""))
    account = WORLD.accounts.get(uid) if uid else None
    if account:
        out["account"] = {"uid": uid, "disabled": account["disabled"], "email_verified": account["email_verified"],
                          "password_matches": None if "password" not in data else account["password"] == data["password"]}
    return JSONResponse(out)


@app.post("/__g6b/g7/verify_custom_token")
async def verify_custom_token(request: Request):
    try:
        return JSONResponse({"ok": True, "claims": WORLD.verify_custom_token((await body(request))["token"])})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": type(exc).__name__})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ["G6B_PORT"]), log_level="warning")
