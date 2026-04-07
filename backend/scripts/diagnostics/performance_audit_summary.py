
import asyncio
import time
import os
import sys

# Add backend to path
sys.path.append(os.getcwd())

from app.services.ai_pipeline import run_ai_pipeline_generator, run_ai_pipeline
from app.services.text_to_speech import synthesize_speech
from app.services.retriever import init_db_pool

async def audit_call(query, mode, lang):
    print(f"--- 🚀 AUDITING: '{query}' ({lang} / {mode}) ---")
    start_time = time.time()
    
    # 1. Pipeline Execution (Sync Part: Language + SQL)
    t_start_pipe = time.time()
    pipe_out = await run_ai_pipeline(query, game_mode=mode, user_lang=lang)
    t_pipe = time.time() - t_start_pipe
    
    # 2. LLM First Chunk Timing
    t_llm = 0.0
    first_text = ""
    if "final_response" in pipe_out:
        # Short-circuited (Instant)
        t_llm = 0.001
        first_text = pipe_out["final_response"]
    else:
        # Generation needed
        t_start_llm = time.time()
        async for chunk in run_ai_pipeline_generator(query, game_mode=mode, user_lang=lang):
            if not first_text:
                t_llm = time.time() - t_start_llm
                first_text = chunk.get('text_chunk', '')
                break
    
    # 3. TTS Generation Time
    t_start_tts = time.time()
    audio_bytes = await synthesize_speech(first_text, language_code=lang[:2].lower())
    t_tts = time.time() - t_start_tts
    
    total_time = time.time() - start_time
    
    return {
        "query": query, "lang": lang,
        "SQL/Pre": t_pipe,
        "LLM_TTFT": t_llm,
        "TTS_LAT": t_tts,
        "TOTAL": total_time,
        "RESP": first_text[:30] + "..."
    }

async def run_audit_suite():
    await init_db_pool()
    
    test_suite = [
        ("Check combo 1 and 29", "Mode 1 Origin Arc( Balakanda)", "English"),
        ("Check combo one and twenty nine", "Mode 1 Origin Arc( Balakanda)", "English"),
        ("மோடு 1-ல் 4 மற்றும் 56 காம்போ என்ன?", "Mode 1 Origin Arc( Balakanda)", "Tamil"),
        ("महारानी 2 और राजा 15 का क्या होगा?", "Mode 1", "Hindi")
    ]
    
    results = []
    for q, m, l in test_suite:
        res = await audit_call(q, m, l)
        results.append(res)
        
    print(f"\n📊 PERFORMANCE AUDIT BREAKDOWN:")
    print(f"{'Language':<10} | {'Type':<12} | {'SQR/Pre':<8} | {'LLM TTFT':<8} | {'TTS Lat':<8} | {'TOTAL':<8} | {'Answer'}")
    print("-" * 100)
    for r in results:
        t_type = "Digits" if any(c.isdigit() for c in r['query']) else "TEXT"
        print(f"{r['lang']:<10} | {t_type:<12} | {r['SQL/Pre']:>6.2f}s | {r['LLM_TTFT']:>6.2f}s | {r['TTS_LAT']:>6.2f}s | {r['TOTAL']:>6.2f}s | {r['RESP']}")
    print("-" * 100)

if __name__ == "__main__":
    asyncio.run(run_audit_suite())
