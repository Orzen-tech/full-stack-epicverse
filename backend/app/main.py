import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import routes
from app.core.config import settings
from app.services.retriever import load_excel_data
from app.services.user_db import init_db
import firebase_admin
from firebase_admin import credentials

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
    print("Server starting up - loading game mode data from Excel files...")
    import asyncio
    from app.services.retriever import init_db_pool
    
    # DB initialization with timeout to prevent server from hanging forever on startup
    try:
        print("Connecting to Database...")
        await asyncio.wait_for(init_db_pool(), timeout=15)
        print("Database Pool initialized.")
        
        print("Initializing Database Tables...")
        await asyncio.wait_for(init_db(), timeout=15)
        print("Database Tables initialized.")
    except asyncio.TimeoutError:
        print("CRITICAL: Database connection timed out. Server running without live DB.")
    except Exception as e:
        print(f"CRITICAL Error during Database initialization: {e}")
    
    await load_excel_data()
    from app.services.wake_word import WakeWordDetector
    WakeWordDetector() # Initialize singleton
    
    print("All game mode data loaded and ready!")
    yield
    print("Server shutting down.")

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
