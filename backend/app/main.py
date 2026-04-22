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
    
    # Infrastructure Initialization with timeouts
    try:
        print("Connecting to Database...")
        await asyncio.wait_for(init_db_pool(), timeout=30) # Increased timeout
        print("Database Pool check finished.")
        
        print("Initializing Database Tables...")
        await asyncio.wait_for(init_db(), timeout=60)
        print("Database Tables initialized.")

        print("Connecting to Redis...")
        await asyncio.wait_for(init_redis(), timeout=15)
    except asyncio.TimeoutError:
        print("CRITICAL: Database/Redis connection timed out. Server running with degraded services.")
    except Exception as e:
        import traceback
        print(f"❌ [DB-CRUCIAL] Pool creation failed!")
        print(f"ERROR: {e}")
        traceback.print_exc()
        _db_pool_failed = True
    
    # Final Transition to Ready State
    await load_excel_data()
    
    print("\n" + "="*50)
    print(f" EPICVERSE BACKEND: {settings.VERSION} ")
    print("="*50)
    print(f" DB Pool:  [{'ACTIVE' if db_pool and not _db_pool_failed else 'FAILED (Critical)'}]")
    print(f" Redis:    [{'ACTIVE' if redis_client else 'OFFLINE (Degraded Mode)'}]")
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

@app.get("/modes")
async def list_modes():
    """Returns the list of available game modes from PostgreSQL."""
    from app.services.retriever import get_available_modes
    modes = await get_available_modes()
    return {"available_modes": sorted(modes)}
