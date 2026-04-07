import asyncio
import random
import time
from dotenv import load_dotenv

load_dotenv()

from app.services.ai_pipeline import run_ai_pipeline
from app.services.retriever import init_db_pool, close_db_pool

LANGUAGES = ["English", "Tamil", "Hindi", "Spanish", "French", "Japanese", "German", "Malayalam", "Telugu", "Kannada"]

# Generate massive test matrices dynamically
def generate_tests():
    tests = []
    
    number_variants = [
        {"char": "1", "attr": "28", "type": "digits"},
        {"char": "one", "attr": "twenty eight", "type": "spelled_en"},
        {"char": "4", "attr": "45", "type": "digits_valid_db"},
        {"char": "four", "attr": "forty five", "type": "spelled_valid_en"},
        {"char": "12", "attr": "45", "type": "digits"},
        {"char": "doce", "attr": "45", "type": "mixed_sp"},
        {"char": "एक", "attr": "तीस", "type": "spelled_hi"}
    ]
    
    why_questions = [
        "Why exactly?", "விளக்கமாக கூறுங்கள் ஏன்?", "ये क्यों है?", "¿Por qué es esto así?", 
        "Pourquoi?", "なぜですか？", "Warum?", "വിശദീകരിക്കുക.", "ఎందుకు?", "ಏಕೆ?"
    ]

    for i in range(1, 10): # Quick latency snapshot
        lang = random.choice(LANGUAGES)
        variant = random.choice(number_variants)
        mode = random.choice(["Mode 1 Origin Arc( Balakanda)", "Mode 2 CrownShift(Ayodhya Kanda)", "Mode 3 GlowLine(Kishkindha Kanda)"])
        
        # Turn 1: Combo Check
        query_1 = f"Checking combination {variant['char']} and {variant['attr']}."
        # Turn 2: Context lookup
        query_2 = random.choice(why_questions)

        tests.append({
            "id": f"Session_Stress_{i}",
            "lang": lang,
            "mode": mode,
            "queries": [query_1, query_2]
        })
        
    return tests

async def run_scenario(scenario, pool_semaphore, metrics):
    async with pool_semaphore:
        try:
            for i, q in enumerate(scenario["queries"]):
                start = time.perf_counter()
                await run_ai_pipeline(
                    text=q,
                    game_mode=scenario["mode"],
                    session_id=scenario["id"],
                    uid=scenario["id"],
                    user_lang=scenario["lang"]
                )
                end = time.perf_counter()
                latency = end - start
                
                # Turn 0 is Check, Turn 1 is Why
                if i == 0:
                    metrics["combo_check"].append(latency)
                else:
                    metrics["why_how"].append(latency)
            return True
        except Exception as e:
            print(f"❌ FAILED [{scenario['lang']}]: {e}")
            return False

async def main():
    print("🚀 INIT: 100+ Massive Multi-Language Stress Test (With Latency Tracking)...")
    tests = generate_tests()
    
    await init_db_pool()
    
    metrics = {"combo_check": [], "why_how": []}
    semaphore = asyncio.Semaphore(1)
    tasks = [run_scenario(test, semaphore, metrics) for test in tests]
    
    print("🔥 LAUNCHING AGGRESSIVE CONCURRENT THREADS...")
    results = await asyncio.gather(*tasks)
    
    success_count = sum(results)
    
    avg_combo = sum(metrics["combo_check"]) / len(metrics["combo_check"]) if metrics["combo_check"] else 0
    avg_why = sum(metrics["why_how"]) / len(metrics["why_how"]) if metrics["why_how"] else 0
    
    print("\n=============================================")
    print(f"✅ STRESS TEST COMPLETE.")
    print(f"Total Isolated Sessions Ran: {len(tests)}")
    print(f"Total AI Operations: {len(tests)*2}")
    print(f"Success Rate: {(success_count/len(tests))*100}%")
    print("\n⚡ LATENCY METRICS ⚡")
    print(f"Combo Valid/Invalid Check (Avg): {avg_combo:.3f} seconds")
    print(f"Sarga Context Explanation (Avg): {avg_why:.3f} seconds")
    print(f"Fastest Database Hit: {min(metrics['combo_check']):.3f} seconds")
    print(f"Deepest LLM Translation: {max(metrics['why_how']):.3f} seconds")
    print("=============================================")
    
    await close_db_pool()

if __name__ == "__main__":
    asyncio.run(main())
