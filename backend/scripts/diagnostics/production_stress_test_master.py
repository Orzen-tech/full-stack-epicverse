
import asyncio
import time
import json
import random
import os
import sys
from typing import List, Dict

# Add current dir to path
sys.path.append(os.getcwd())

from app.services.ai_pipeline import run_ai_pipeline
from app.services.retriever import init_db_pool

# Test Configuration
CONCURRENCY = 5
TOTAL_TESTS = 210
LANGUAGES = {
    "en": "English",
    "ta": "Tamil",
    "hi": "Hindi",
    "es": "Spanish",
    "ja": "Japanese",
    "fr": "French",
    "te": "Telugu"
}

QUERY_TEMPLATES = {
    "en": "Check combo {c1} and {a1}",
    "ta": "காம்போ {c1} మరియు {a1} சரிபார்க்கவும்",
    "hi": "कॉम्బో {c1} और {a1} की जांच करें",
    "es": "Verificar combo {c1} y {a1}",
    "ja": "コンボ {c1} と {a1} を確認してください",
    "fr": "Vérifier le combo {c1} et {a1}",
    "te": "కాంబో {c1} మరియు {a1}ని తనిఖీ చేయండి"
}

async def run_single_test(session, test_case: Dict, lang_code: str, user_id: str):
    c1 = test_case['character_card_number']
    a1 = test_case['attribute_card_no']
    mode = test_case['gameplay_mode']
    db_status = test_case['final_status']
    
    lang_name = LANGUAGES[lang_code]
    query = QUERY_TEMPLATES[lang_code].format(c1=c1, a1=a1)
    
    start_time = time.time()
    try:
        # We call the core pipeline directly
        response = await run_ai_pipeline(
            text=query,
            game_mode=mode,
            session_id=f"stress_{user_id}",
            uid=user_id,
            user_lang=lang_name
        )
        end_time = time.time()
        
        duration = end_time - start_time
        ai_resp = response.get("final_response", "").lower()
        
        # Simple logical check
        is_success = False
        if db_status.lower() in ai_resp or ("valid" in ai_resp and db_status.lower() == "valid"):
            is_success = True
            
        return {
            "user": user_id,
            "query": query,
            "mode": mode,
            "lang": lang_code,
            "expected": db_status,
            "actual": response.get("final_response", ""),
            "latency": round(duration, 3),
            "success": is_success
        }
    except Exception as e:
        return {"error": str(e), "latency": time.time() - start_time}

async def master_test_suite():
    print(f"--- Starting EpicVerse Final Stress Test ({TOTAL_TESTS} cases) ---")
    pool = await init_db_pool()
    async with pool.acquire() as conn:
        # Fetch 210 diverse combos
        records = await conn.fetch('SELECT gameplay_mode, character_card_number, attribute_card_no, final_status FROM card_combos ORDER BY RANDOM() LIMIT $1', TOTAL_TESTS)
        test_cases = [dict(r) for r in records]

    results = []
    semaphore = asyncio.Semaphore(CONCURRENCY)
    
    async def sem_run(tc, lang, uid):
        async with semaphore:
            return await run_single_test(None, tc, lang, uid)

    tasks = []
    for i, case in enumerate(test_cases):
        lang_code = random.choice(list(LANGUAGES.keys()))
        uid = f"senior_tester_{i % 10}" # Rotate 10 users
        tasks.append(sem_run(case, lang_code, uid))

    print(f"Simulating {CONCURRENCY} concurrent users...")
    results = await asyncio.gather(*tasks)
    
    await pool.close()
    
    # Generate Report summary
    with open("stress_test_raw_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
        
    print(f"--- Completed {len(results)} tests ---")
    print(f"Analysis saved to stress_test_raw_results.json")

if __name__ == "__main__":
    asyncio.run(master_test_suite())
