import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def view_example():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    
    uid = 'fag5kbXfMzPNb0AxxhIcugYQ3Z03'
    
    print("\n--- [TABLE: users] Example Entry ---")
    user = await conn.fetchrow("SELECT * FROM users WHERE uid = $1", uid)
    if user:
        for k, v in dict(user).items():
            print(f"{k.ljust(25)}: {v}")
    
    print("\n--- [TABLE: chat_history] Most Recent Interaction ---")
    chats = await conn.fetch("SELECT * FROM chat_history WHERE uid = $1 ORDER BY created_at DESC LIMIT 2", uid)
    if chats:
        for c in chats:
            print("-" * 60)
            for k, v in dict(c).items():
                val = str(v)[:100] + "..." if len(str(v)) > 100 else v
                print(f"{k.ljust(25)}: {val}")

    print("\n--- [TABLE: card_combos] Example Spreadsheet Match (1 and 28) ---")
    row = await conn.fetchrow("""
        SELECT gameplay_mode, character, character_card_number, virtue_karma, virtue_karma_card_number, validation_reason 
        FROM card_combos 
        WHERE character_card_number = 1 AND virtue_karma_card_number = 28
        LIMIT 1
    """)
    if row:
        for k, v in dict(row).items():
            val = str(v)[:150] + "..." if len(str(v)) > 150 else v
            print(f"{k.ljust(25)}: {val}")
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(view_example())
