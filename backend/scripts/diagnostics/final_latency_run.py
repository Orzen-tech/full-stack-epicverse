
import asyncio
import time
import os
import sys

# Add backend to path
sys.path.append(os.getcwd())

from app.services.ai_pipeline import run_ai_pipeline_generator
from app.services.retriever import init_db_pool

async def measure(query, label):
    start_time = time.time()
    first_chunk_time = None
    
    # print(f"[{label}] Querying: '{query}'...")
    async for chunk in run_ai_pipeline_generator(query, user_lang="English"):
        if first_chunk_time is None:
            first_chunk_time = time.time() - start_time
            # print(f"[{label}] ⏱️ FIRST CHUNK: {first_chunk_time:.3f}s")
            break # Just measure first chunk for latency
    return first_chunk_time

async def benchmark_streaming():
    print("--- 🏁 Final Turbo-Mode Latency Benchmark ---")
    await init_db_pool()
    
    query = "Check combo 4 and 56 in Origin Arc"
    
    # 1. COLD START (with library/init overhead)
    cold_time = await measure(query, "COLD")
    
    # 2. WARM START (Real-world production state)
    warm_time = await measure(query, "WARM")
    
    print(f"\n📊 RESULTS:")
    print(f"- COLD START LATENCY: {cold_time:.3f}s")
    print(f"- **WARM PRODUCTION LATENCY: {warm_time:.3f}s**")
    
    status = "🚀 EXCELLENT" if warm_time < 2.0 else ("✅ GOOD" if warm_time < 3.0 else "⚠️ NEEDS POLISH")
    print(f"\nSTATUS: {status}")
    print(f"--- 🏁 Benchmark Complete ---")

if __name__ == "__main__":
    asyncio.run(benchmark_streaming())
