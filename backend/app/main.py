import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from app.api import routes
from app.core.config import settings
from app.services.retriever import load_excel_data
from app.services.user_db import init_db
from app.services.db_pool import get_pool, close_pool
import firebase_admin
from firebase_admin import credentials

# Initialize Firebase Admin
try:
    if not firebase_admin._apps:
        cred = credentials.ApplicationDefault()
        firebase_project_id = os.getenv("FIREBASE_PROJECT_ID", "")
        options = {"projectId": firebase_project_id} if firebase_project_id else {}
        firebase_admin.initialize_app(cred, options)
except Exception as e:
    print(f"Firebase Init Warning: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Server starting up...")
    try:
        await get_pool()
        print("[DB] Connection pool ready.")
    except Exception as e:
        print(f"⚠️ [DB] Pool init failed: {e}")
    try:
        await init_db()
    except Exception as e:
        print(f"⚠️ [DB] init_db failed (non-fatal): {e}")
    try:
        await load_excel_data()
        print("Server ready.")
    except Exception as e:
        print(f"⚠️ [DATA] load_excel_data failed: {e}")
    yield
    await close_pool()
    print("Server shutting down.")

_ENV = os.getenv("ENV", "prod").lower()
_IS_PROD = _ENV not in ("dev", "development", "local")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    # Hide OpenAPI schema, Swagger UI, and ReDoc in production so we don't
    # hand attackers a machine-readable map of every endpoint. Set ENV=dev in
    # the environment to re-enable locally.
    openapi_url=None if _IS_PROD else f"{settings.API_V1_STR}/openapi.json",
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(routes.router, prefix=settings.API_V1_STR)

@app.get("/")
def root():
    return {"message": "Welcome to the Game Guide AI Voice Agent Backend"}

@app.get("/health")
async def health():
    import time
    from app.services.db_pool import get_pool
    from app.services.retriever import init_redis

    result = {
        "status": "ok",
        "database": "unknown",
        "redis": "unknown",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Check PostgreSQL
    try:
        pool = await get_pool()
        t0 = time.monotonic()
        await pool.fetchval("SELECT 1")
        result["database"] = f"connected ({round((time.monotonic()-t0)*1000)}ms)"
    except Exception as e:
        result["database"] = f"error: {str(e)}"
        result["status"] = "degraded"

    # Check Redis
    try:
        redis = await init_redis()
        if redis is None:
            result["redis"] = "disabled or offline"
        else:
            t0 = time.monotonic()
            await redis.ping()
            result["redis"] = f"connected ({round((time.monotonic()-t0)*1000)}ms)"
    except Exception as e:
        result["redis"] = f"error: {str(e)}"

    return result

@app.get("/delete-account", response_class=HTMLResponse)
def delete_account_page():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Delete Account — EpicVerse</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #1B0C2D;
      color: #E8E0F0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .card {
      background: #2A1245;
      border: 1px solid #3D1E6B;
      border-radius: 20px;
      max-width: 560px;
      width: 100%;
      padding: 48px 40px;
    }
    .logo {
      font-size: 28px;
      font-weight: 700;
      color: #C084FC;
      margin-bottom: 8px;
      letter-spacing: 1px;
    }
    .tagline {
      font-size: 13px;
      color: #9B7DC4;
      margin-bottom: 36px;
    }
    h1 {
      font-size: 22px;
      font-weight: 600;
      color: #F3E8FF;
      margin-bottom: 12px;
    }
    p {
      font-size: 15px;
      color: #C4B5D8;
      line-height: 1.7;
      margin-bottom: 20px;
    }
    .steps {
      background: #1B0C2D;
      border-radius: 12px;
      padding: 20px 24px;
      margin-bottom: 28px;
    }
    .steps h2 {
      font-size: 14px;
      font-weight: 600;
      color: #C084FC;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 14px;
    }
    .step {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 12px;
    }
    .step:last-child { margin-bottom: 0; }
    .step-num {
      background: #6D28D9;
      color: #fff;
      font-size: 12px;
      font-weight: 700;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      margin-top: 2px;
    }
    .step-text {
      font-size: 14px;
      color: #D4C5E8;
      line-height: 1.5;
    }
    .step-text strong { color: #F3E8FF; }
    .note {
      background: #3D1E6B22;
      border-left: 3px solid #6D28D9;
      border-radius: 4px;
      padding: 14px 16px;
      font-size: 13px;
      color: #B89FD4;
      line-height: 1.6;
      margin-bottom: 28px;
    }
    .contact {
      font-size: 14px;
      color: #9B7DC4;
      text-align: center;
    }
    .contact a {
      color: #C084FC;
      text-decoration: none;
      font-weight: 500;
    }
    .contact a:hover { text-decoration: underline; }
    .divider {
      border: none;
      border-top: 1px solid #3D1E6B;
      margin: 28px 0;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">EpicVerse</div>
    <div class="tagline">AI Voice Companion</div>

    <h1>Delete Your Account</h1>
    <p>
      You can delete your EpicVerse account directly from the app.
      Deleting your account will permanently remove all your data,
      including your profile, conversation history, and preferences.
    </p>

    <div class="steps">
      <h2>How to delete from the app</h2>
      <div class="step">
        <div class="step-num">1</div>
        <div class="step-text">Open the <strong>EpicVerse</strong> app and sign in</div>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <div class="step-text">Tap your profile icon to go to <strong>Settings</strong></div>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <div class="step-text">Scroll down and tap <strong>Delete Account</strong></div>
      </div>
      <div class="step">
        <div class="step-num">4</div>
        <div class="step-text">Confirm deletion — your account will be <strong>permanently deleted within 30 days</strong></div>
      </div>
    </div>

    <div class="note">
      If you sign back into the app within 30 days, your deletion request will be
      automatically cancelled and your account will be fully restored.
    </div>

    <hr class="divider" />

    <div class="contact">
      Lost access to your account?<br />
      Email us at <a href="mailto:support@kriyora.com">support@kriyora.com</a>
      and we will delete your data within 7 business days.
    </div>
  </div>
</body>
</html>"""


@app.get("/modes")
async def list_modes():
    """Returns the list of available game modes from PostgreSQL."""
    from app.services.retriever import get_available_modes
    modes = await get_available_modes()
    return {"available_modes": sorted(modes)}
