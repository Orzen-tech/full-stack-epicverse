import asyncio
import random
from app.services.ai_pipeline import run_ai_pipeline, VALID_MSGS, INVALID_MSGS, EXCLUDE_MSGS
from app.services.retriever import init_db_pool, close_db_pool

async def verify_logic():
    await init_db_pool()
    uid = "LogicVerifier"
    session_id = "test_logic_123"
    
    print("--- STEP 1: Combo Check (20 and 45) ---")
    res1 = await run_ai_pipeline("20 and 45 combo", "Mode 1 Origin Arc( Balakanda)", session_id, uid, "English")
    ans1 = res1.get("final_response", "")
    print(f"AI: {ans1}")
    
    # Check if ans1 is in one of the lists
    is_valid = ans1 in VALID_MSGS
    is_invalid = ans1 in INVALID_MSGS
    is_exclude = ans1 in EXCLUDE_MSGS
    
    if is_valid: print("MATCHED: Found in VALID_MSGS list.")
    elif is_invalid: print("MATCHED: Found in INVALID_MSGS list.")
    elif is_exclude: print("MATCHED: Found in EXCLUDE_MSGS list.")
    else: print("❌ ERROR: Response was not one of the randomized predefined messages.")

    print("\n--- STEP 2: Context Question (Why?) ---")
    res2 = await run_ai_pipeline("Why?", "Mode 1 Origin Arc( Balakanda)", session_id, uid, "English")
    ans2 = res2.get("final_response", "")
    print(f"AI: {ans2}")
    
    # Check for "STATUS:" or "REASON:" labels
    if "STATUS:" in ans2 or "REASON:" in ans2:
        print("❌ ERROR: Technical labels found in response.")
    else:
        print("✅ SUCCESS: Response is clean of technical labels.")

    await close_db_pool()

if __name__ == "__main__":
    asyncio.run(verify_logic())
