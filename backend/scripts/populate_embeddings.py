import asyncpg
import asyncio
import os
import openai
import json
from dotenv import load_dotenv

load_dotenv()

openai_client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def get_embedding(text):
    if not text or not text.strip():
        return None
    try:
        response = await openai_client.embeddings.create(
            input=[text.replace("\n", " ")],
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Embedding error: {e}")
        return None

async def run():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    
    # Fetch rows that don't have embeddings yet
    rows = await conn.fetch("SELECT id, validation_reason, app_explanation FROM card_combos WHERE embedding IS NULL")
    print(f"Total rows to embed: {len(rows)}")
    
    batch_size = 50
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i+batch_size]
        print(f"Processing batch {i//batch_size + 1}/{(len(rows)-1)//batch_size + 1}...")
        
        for row in batch:
            # Combine fields for better semantic search
            reason = row['validation_reason'] or ''
            expl = row['app_explanation'] or ''
            text_to_embed = f"{reason} {expl}".strip()
            
            if not text_to_embed:
                continue
                
            embedding = await get_embedding(text_to_embed)
            if embedding:
                # Convert list to pgvector string format: [val, val, ...]
                vec_str = "[" + ",".join(map(str, embedding)) + "]"
                await conn.execute("UPDATE card_combos SET embedding = $1 WHERE id = $2", vec_str, row['id'])
        
        # Small sleep
        await asyncio.sleep(0.2)

    print("Embedding population complete.")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
