import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from app.services.ai_pipeline import run_ai_pipeline
from app.services.retriever import init_db_pool, close_db_pool

async def run_massive_language_test():
    print("🚀 INIT: Deep Tester Multi-Language Mode...")
    pool = await init_db_pool()
    uid = "clean_slate_user_v2"
    
    # 1. Fetch a known good valid combo directly from the DB
    row = await pool.fetchrow("SELECT gameplay_mode, character_card_number, attribute_card_no, revised_scholar_reason FROM card_combos LIMIT 1")
    if not row:
        print("DATABASE IS EMPTY!")
        return
        
    db_mode = row["gameplay_mode"]
    db_char = row["character_card_number"]
    db_attr = row["attribute_card_no"]
    db_reason = row["revised_scholar_reason"]
    
    print(f"=============================================")
    print(f"🏆 GROUND TRUTH FROM POSTGRESQL 🏆")
    print(f"Mode: {db_mode} | Combo: {db_char} & {db_attr}")
    print(f"Reason: {db_reason}")
    print(f"=============================================\n")

    # Front-end style input
    fake_frontend_mode = f"Mode 1 {db_mode}" # Just simulating the dropdown wrapper
    
    # Session starts!
    # Turn 1: Establish Context (English)
    res1 = await run_ai_pipeline(
        text=f"I am checking {db_char} and {db_attr}",
        game_mode=fake_frontend_mode,
        session_id="multi_test_sess",
        uid=uid,
        user_lang="English"
    )
    print(f"[-TURN 1 Context Setting-] {res1.get('final_response')}")
    
    # 10 Different Languages asking "Why? / Explain this combo"
    languages = [
        {"lang": "Tamil", "q": "ஏன்? விளக்கமாக கூறுங்கள்."},
        {"lang": "Hindi", "q": "ये क्यों है? मुझे समझाएं।"},
        {"lang": "Telugu", "q": "ఇది ఎందుకు? నాకు వివరించండి."},
        {"lang": "Malayalam", "q": "എന്തുകൊണ്ട്? ദയവായി വിശദീകരിക്കുക."},
        {"lang": "Kannada", "q": "ಇದು ಏಕೆ? ವಿವರಿಸಿ."},
        {"lang": "French", "q": "Pourquoi? Expliquez."},
        {"lang": "Spanish", "q": "¿Por qué? Explícamelo."},
        {"lang": "German", "q": "Warum? Bitte erkläre das."},
        {"lang": "Japanese", "q": "なぜですか？説明してください。"},
        {"lang": "English", "q": "Why exactly is this combo valid?"}
    ]
    
    for l in languages:
        print(f"\n🌍 Testing Language: {l['lang'].upper()}...")
        print(f"[Query]: {l['q']}")
        res = await run_ai_pipeline(
            text=l["q"],
            game_mode=fake_frontend_mode,
            session_id="multi_test_sess",
            uid=uid,
            user_lang=l["lang"]
        )
        print(f"[DETECTED ISO]: {res.get('detected_lang')} | [AI OUTPUT]: {res.get('final_response')}")

    await close_db_pool()
    print("\n✅ Multi-Language Verification Complete.")

if __name__ == "__main__":
    asyncio.run(run_massive_language_test())
