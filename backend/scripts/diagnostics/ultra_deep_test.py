import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from app.services.ai_pipeline import run_ai_pipeline
from app.services.retriever import init_db_pool, close_db_pool

async def run_ultra_test():
    print("🚀 INIT: Ultra Deep Tester (Sequential Modes & NLP Verification)...")
    await init_db_pool()
    uid = "ultra_tester_123"

    tests = [
        # SCENARIO 1: ENGLISH MODE 1, Spelled Out Numbers
        {
            "mode": "Mode 1 Origin Arc( Balakanda)",
            "lang": "English",
            "queries": [
                "I am checking one and twenty eight.", # Check number spelled out
                "Why exactly?"
            ]
        },
        # SCENARIO 2: TAMIL MODE 1, Digits
        {
            "mode": "Mode 1 Origin Arc( Balakanda)",
            "lang": "Tamil",
            "queries": [
                "நான் 1 மற்றும் 28 ஐ பயன்படுத்துகிறேன்.", 
                "விளக்கமாக கூறுங்கள் ஏன்?"
            ]
        },
        # SCENARIO 3: HINDI MODE 2, Spelled Out Numbers equivalent test
        {
            "mode": "Mode 2 CrownShift (Ayodhya Kanda)",
            "lang": "Hindi",
            "queries": [
                "मैं एक और छत्तीस की जाँच कर रहा हूँ।", # ek aur chhattis (one and thirty six)
                "ये क्यों है? मुझे समझाएं।"
            ]
        },
        # SCENARIO 4: SPANISH MODE 3
        {
            "mode": "Mode 3 GlowLine (Kishkindha Kanda)",
            "lang": "Spanish",
            "queries": [
                "Revisando doce y 45.", # doce (12) and 45
                "¿Por qué es esto así?"
            ]
        }
    ]

    for scenario in tests:
        print(f"\n=============================================")
        print(f"🎬 SCENARIO: Mode: {scenario['mode']} | Language: {scenario['lang'].upper()}")
        print(f"=============================================")
        for q in scenario["queries"]:
            print(f"[{scenario['lang'].upper()}] USER: {q}")
            res = await run_ai_pipeline(
                text=q,
                game_mode=scenario["mode"],
                session_id="ultra_sess_1",
                uid=uid,
                user_lang=scenario["lang"]
            )
            print(f"[AI RESPONSE] -> {res.get('final_response')}")
        print("\n")

    await close_db_pool()
    print("✅ Ultra Test Complete.")

if __name__ == "__main__":
    asyncio.run(run_ultra_test())
