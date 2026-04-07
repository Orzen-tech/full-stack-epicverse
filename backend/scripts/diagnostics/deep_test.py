import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from app.services.ai_pipeline import run_ai_pipeline
from app.services.retriever import init_db_pool, close_db_pool

async def deep_test():
    print("🚀 INIT: Deep Tester Mode Activated...")
    await init_db_pool()
    uid = "deep_test_user_777"
    
    tests = [
        {"mode": "Mode 1 Origin Arc( Balakanda)", "lang": "English", "query": "I am putting 1 and 29. Is it valid?"},
        {"mode": "Mode 1 Origin Arc( Balakanda)", "lang": "English", "query": "Why? How is it valid?"},
        {"mode": "Mode 2", "lang": "Tamil", "query": "நான் 1 மற்றும் 29 ஐ பயன்படுத்துகிறேன்."},
        {"mode": "Mode 2", "lang": "Tamil", "query": "ஏன்?"},
    ]

    for t in tests:
        print(f"\n=============================================")
        print(f"[{t['lang'].upper()}] USER (Mode: {t['mode']}): {t['query']}")
        result = await run_ai_pipeline(
            text=t["query"],
            game_mode=t["mode"],
            session_id="test_session123",
            uid=uid,
            user_lang=t["lang"]
        )
        print(f"[AI RESPONSE] -> {result.get('final_response')}")
        print(f"[DETECTED LANG] -> {result.get('detected_lang')}")
        print(f"=============================================\n")

    await close_db_pool()
    print("✅ Testing Complete.")

if __name__ == "__main__":
    asyncio.run(deep_test())
