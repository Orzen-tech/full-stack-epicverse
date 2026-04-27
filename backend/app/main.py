import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import routes
from app.core.config import settings
from app.services.retriever import close_db_pool, close_redis, init_redis, load_excel_data, redis_client
from app.services.user_db import init_db
import firebase_admin
from firebase_admin import credentials
from app.services.retriever import db_pool, _db_pool_failed

# Initialize Firebase Admin
try:
    if not firebase_admin._apps:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
except Exception as e:
    print(f"Firebase Init Warning: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs on server startup: preloads all Excel game mode files into RAM."""
    import asyncio
    from app.services.retriever import init_db_pool
    
    # 1. Database Initialization (Backgrounded to prevent Cloud Run startup timeouts)
    async def run_db_init():
        import app.services.retriever as retriever
        try:
            print("Connecting to Database in background...")
            await retriever.init_db_pool()
            if retriever.db_pool:
                print("Database Pool ready. Initializing Tables...")
                await asyncio.wait_for(init_db(), timeout=30)
                print("Database Tables initialized.")
            else:
                print("⚠️ [DB] Pool not ready. Will retry on next request.")
        except Exception as e:
            print(f"❌ [DB-CRUCIAL] Background Database Initialization Failed: {e}")
            retriever._db_pool_failed = True

    asyncio.create_task(run_db_init())

    # 2. Redis Initialization (Non-Critical, Backgrounded)
    async def run_redis_init():
        try:
            print("Connecting to Redis in background...")
            await asyncio.wait_for(init_redis(), timeout=15)
        except Exception as e:
            print(f"⚠️ [INFRA] Redis Offline: Disabling Semantic Cache. ({e})")
    
    asyncio.create_task(run_redis_init())
    
    # Final Transition to Ready State (Backgrounded)
    import app.services.retriever as retriever
    async def run_data_load():
        try:
            print("Loading Excel datasets in background...")
            await retriever.load_excel_data()
            print("==================================================")
            print(" EPICVERSE BACKEND READY")
            print("==================================================")
        except Exception as e:
            print(f"❌ [DATA] Failed to load Excel data: {e}")

    asyncio.create_task(run_data_load())
    
    print("\n" + "="*50)
    print(f" EPICVERSE BACKEND: {settings.VERSION} ")
    print("="*50)
    print(f" DB Pool:  [{'ACTIVE' if retriever.db_pool and not retriever._db_pool_failed else 'FAILED (Critical)'}]")
    print(f" Redis:    [{'ACTIVE' if retriever.redis_client else 'OFFLINE (Degraded Mode)'}]")
    print(f" Datasets: [LOADED]")
    print("="*50 + "\n")
    
    yield
    
    print("Server shutting down.")
    await close_redis()
    await close_db_pool()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "https://epicverse-backend-721191424605.us-central1.run.app").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(routes.router, prefix=settings.API_V1_STR)

@app.get("/")
async def root():
    from app.services.retriever import db_pool, redis_client, _db_pool_failed
    return {
        "message": "EpicVerse Backend API",
        "version": settings.VERSION,
        "status": {
            "database": "CONNECTED" if db_pool else ("FAILED" if _db_pool_failed else "INITIALIZING"),
            "redis": "CONNECTED" if redis_client else "OFFLINE"
        }
    }

@app.get("/modes")
async def list_modes():
    """Returns the list of available game modes from PostgreSQL."""
    from app.services.retriever import get_available_modes
    modes = await get_available_modes()
    return {"available_modes": sorted(modes)}
