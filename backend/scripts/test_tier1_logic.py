import asyncio
import json
import random
import argparse
from app.services.retriever import query_postgres_database

# SCHOLARLY PREFERRED MESSAGES
VALID_MSGS = [
    "Ah... rightly placed. Valid.", "Yes... a true, valid combo.", "Proceed... this is valid.",
    "You learn well... this is valid.", "Wisely played... valid.", "Accepted... it is valid.",
    "Good... this holds valid.", "On point... this is valid.", "Good flow... valid.",
    "That fits... valid.", "Well aligned... valid."
]
INVALID_MSGS = [
    "Hmm... invalid combo.", "Not quite... invalid combo.", "Almost... invalid combo.",
    "That slipped... invalid combo.", "Off track... invalid combo.", "Doesn't align... invalid combo.",
    "Try again... invalid combo.", "Close, but... invalid combo.", "That didn't land... invalid combo.",
    "Bit off... invalid combo.", "Doesn't quite work... invalid combo."
]
EXCLUDE_MSGS = [
    "Close! Valid, not executed yet.", "Almost there... valid, not executed.",
    "Good one... valid, not executed.", "Nearly there! Valid, not executed.",
    "On track... valid, not executed.", "So near... valid, not executed.",
    "You're close... valid, not executed.", "Almost right... valid, not executed.",
    "Getting there... valid, not executed.", "Not quite... valid, not executed.",
    "Close enough... valid, not executed."
]

async def test_tier1(mode, card1, card2):
    print(f"\n[DIAGNOSTIC] Mode: {mode} | Combination: {card1} + {card2}")
    
    # 1. Backend Lookup
    raw_result = await query_postgres_database(mode, str(card1), str(card2))
    
    # 2. Logic Simulation
    try:
        data = json.loads(raw_result)
        status = data.get("status", "Invalid")
        
        # Select Random Preferred Message
        msg_list = VALID_MSGS if status == "Valid" else INVALID_MSGS
        if status == "Exclude": msg_list = EXCLUDE_MSGS
        random_msg = random.choice(msg_list)
        
        # Output ONLY Tier 1
        print(f"--- TIER 1 RESPONSE SIMULATION ---")
        print(f"AI PREVIEW: \"{random_msg}\"")
        print(f"SCRIPTURE:  \"{data.get('final_segment')}\"")
        print(f"--- END SIMULATION ---\n")
        
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--card1", required=True)
    parser.add_argument("--card2", required=True)
    args = parser.parse_args()
    
    asyncio.run(test_tier1(args.mode, args.card1, args.card2))
