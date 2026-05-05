import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
def health():
    return {"status": "ok"}

@app.get("/modes")
async def list_modes():
    """Returns the list of available game modes from PostgreSQL."""
    from app.services.retriever import get_available_modes
    modes = await get_available_modes()
    return {"available_modes": sorted(modes)}
