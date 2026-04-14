import asyncio
import random
import string
import sys
import os

# Add parent directory to path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.user_db import get_db_pool, init_db

def generate_random_code(length=8):
    """Generates a random alphanumeric code."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

async def create_invites(count=10, max_uses=1, prefix="EPIC"):
    """Creates a batch of invite codes in the database."""
    await init_db()
    pool = await get_db_pool()
    
    if not pool:
        print("Error: Could not connect to database.")
        return

    codes = []
    async with pool.acquire() as conn:
        for _ in range(count):
            code = f"{prefix}-{generate_random_code(6)}"
            try:
                await conn.execute('''
                    INSERT INTO invite_codes (code, max_uses)
                    VALUES ($1, $2)
                ''', code, max_uses)
                codes.append(code)
                print(f"Created: {code} (Max uses: {max_uses})")
            except Exception as e:
                print(f"Failed to create {code}: {e}")
    
    print("\n" + "="*30)
    print(f"SUCCESSFULLY GENERATED {len(codes)} CODES")
    print("="*30)
    for c in codes:
        print(c)
    print("="*30)

if __name__ == "__main__":
    # Settings
    num_codes = 5
    if len(sys.argv) > 1:
        num_codes = int(sys.argv[1])
        
    asyncio.run(create_invites(count=num_codes))
