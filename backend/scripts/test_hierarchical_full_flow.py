import asyncio
import json
import random
from app.services.retriever import query_postgres_database

# SCHOLARLY PREFERRED MESSAGES
VALID_MSGS = ["Ah... rightly placed.", "Good flow... valid.", "Yes... a true, valid combo."]

async def simulate_hierarchical_minimalist_flow(mode, card1, card2):
    results = {
        "mode_selected": mode,
        "first_q": f"Check {card1} and {card2}",
        "second_q": "Why?",
        "ai_queried_mode": None,
        "first_response": None,
        "second_response": None
    }
    
    # --- TURN 1: COMBO CHECK ---
    raw_result = await query_postgres_database(mode, str(card1), str(card2))
    data = json.loads(raw_result)
    results["ai_queried_mode"] = data.get("gameplay_mode", mode)
    
    status = data.get("status", "Invalid")
    pref_msg = random.choice(VALID_MSGS) if status == "Valid" else "Hmm... invalid combo."
    
    # AI Logic: ONLY the minimalist pref_msg
    results["first_response"] = pref_msg
    
    # --- TURN 2: WHY? ---
    # AI Logic: reveal BOTH final_segment and revised_scholar_reason
    results["second_response"] = f"{data.get('final_segment')} {data.get('revised_scholar_reason')}"
    
    return results

async def run_suite():
    test_cases = [
        ('Origin Arc( Balakanda)', 7, 25),
        ('CrownShift (Ayodhya Kanda)', 5, 55),
        ('GlowLine (Kishkindha Kanda)', 4, 25)
    ]
    
    final_output = []
    for mode, c1, c2 in test_cases:
        res = await simulate_hierarchical_minimalist_flow(mode, c1, c2)
        final_output.append(res)
        
    print(json.dumps(final_output, indent=2))

if __name__ == "__main__":
    asyncio.run(run_suite())
