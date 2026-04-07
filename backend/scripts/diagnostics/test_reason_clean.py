import asyncio
from app.services.ai_pipeline import run_ai_pipeline
from app.services.retriever import init_db_pool, close_db_pool

async def test_reason_formatting():
    await init_db_pool()
    uid = "FormatterTest"
    session_id = "test_123"
    
    print("Step 1: Check 4 and 56...")
    await run_ai_pipeline("4 and 56 combo", "Mode 1", session_id, uid, "English")
    
    print("Step 2: Ask Why...")
    res = await run_ai_pipeline("Why?", "Mode 1", session_id, uid, "English")
    
    print("\n[AI RESPONSE]:")
    print(res.get("final_response"))
    print("--------------------")
    
    await close_db_pool()

if __name__ == "__main__":
    asyncio.run(test_reason_formatting())
