import asyncio
import time
import json
from dotenv import load_dotenv

load_dotenv()

from app.services.ai_pipeline import run_ai_pipeline
from app.services.retriever import init_db_pool, close_db_pool

async def test_session(lang_name, combo_query, why_query, user_id):
    uid = f"Tester_{user_id}"
    session_id = f"FinalCheck_{user_id}"
    
    print(f"--- Language: {lang_name} ---")
    
    # Question 1: Combo Check
    start1 = time.perf_counter()
    res1 = await run_ai_pipeline(
        text=combo_query,
        game_mode="Mode 1 Origin Arc( Balakanda)",
        session_id=session_id,
        uid=uid,
        user_lang=lang_name
    )
    end1 = time.perf_counter()
    lat1 = end1 - start1
    print(f"Q1 ({combo_query}): {lat1:.2f}s")
    
    # Organic delay
    await asyncio.sleep(1)
    
    # Question 2: Why?
    start2 = time.perf_counter()
    res2 = await run_ai_pipeline(
        text=why_query,
        game_mode="Mode 1 Origin Arc( Balakanda)",
        session_id=session_id,
        uid=uid,
        user_lang=lang_name
    )
    end2 = time.perf_counter()
    lat2 = end2 - start2
    print(f"Q2 ({why_query}): {lat2:.2f}s")
    print(f"Response: {res2.get('final_response', '')[:100]}...\n")
    
    return {
        "lang": lang_name,
        "q1_time": lat1,
        "q2_time": lat2
    }

async def run_test_suite():
    await init_db_pool()
    
    tests = [
        ("English", "4 and 56 combo", "Why?"),
        ("Tamil", "4 மற்றும் 56 சேர்க்கை", "ஏன்?"),
        ("Hindi", "4 और 56 कॉम्बो", "क्यों?"),
        ("Spanish", "combo de 4 y 56", "¿Por qué?"),
        ("Japanese", "4と56のコンボ", "なぜですか？")
    ]
    
    results = []
    for i, (lang, q1, q2) in enumerate(tests):
        res = await test_session(lang, q1, q2, i)
        results.append(res)
    
    print("=============================================")
    print("📊 FINAL LATENCY SUMMARY")
    print("=============================================")
    for r in results:
        print(f"{r['lang']:<10} | Q1: {r['q1_time']:.2f}s | Q2: {r['q1_time']:.2f}s")
    print("=============================================")
    
    await close_db_pool()

if __name__ == "__main__":
    asyncio.run(run_test_suite())
