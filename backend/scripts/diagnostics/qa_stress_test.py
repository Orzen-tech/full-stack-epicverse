import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from app.services.ai_pipeline import run_ai_pipeline, _SESSION_CONTEXT
from app.services.retriever import init_db_pool, close_db_pool

async def run_qa_test():
    print("🚀 INIT: Senior QA Stress & Edge Case Tester...")
    await init_db_pool()
    uid = "qa_tester_destructive"

    tests = [
        # SCENARIO 1: MIXED NLP (Digit + Spelled word)
        {
            "name": "Mixed NLP Input (Digit + Word)",
            "lang": "English",
            "queries": [
                "Testing combination 4 and forty five.",
                "Why?"
            ]
        },
        # SCENARIO 2: OUT OF BOUNDS / INVALID TYPES
        {
            "name": "SQL Injection / Out of Bounds simulation",
            "lang": "English",
            "queries": [
                "I am placing character 9999999 and attribute -5.",
                "How does that work?"
            ]
        },
        # SCENARIO 3: CONTEXT CONFUSION (Distraction)
        {
            "name": "Context Disruption & Recovery",
            "lang": "English",
            "queries": [
                "check 4 and 45",
                "What is the capital of France?", # Out of scope distraction
                "Why is that combo invalid? Explain." # Context recovery request
            ]
        },
        # SCENARIO 4: RAPID CHAT & PARTIAL INPUT
        {
            "name": "Partial Number Input",
            "lang": "Tamil",
            "queries": [
                "நான் 4 ஐ தேர்ந்தெடுத்தேன்.", # "I chose 4." (only 1 number)
                "இப்போது 45 இடுகிறேன்." # "Now I place 45."
            ]
        }
    ]

    for scenario in tests:
        print(f"\n=============================================")
        print(f"🎬 SCENARIO: {scenario['name']}")
        print(f"=============================================")
        
        # Clear memory before each isolated test
        if uid in _SESSION_CONTEXT:
            del _SESSION_CONTEXT[uid]

        for q in scenario["queries"]:
            print(f"[USER -> {scenario['lang'].upper()}]: {q}")
            res = await run_ai_pipeline(
                text=q,
                game_mode="Mode 1 Origin Arc( Balakanda)",
                session_id=f"qa_sess_{scenario['name']}",
                uid=uid,
                user_lang=scenario['lang']
            )
            print(f"[SYSTEM]: {res.get('final_response')}")
        print("\n")

    await close_db_pool()
    print("✅ Senior QA Test Complete.")

if __name__ == "__main__":
    asyncio.run(run_qa_test())
