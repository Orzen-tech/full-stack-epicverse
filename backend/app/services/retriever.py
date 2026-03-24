import os
import asyncpg
import openai
from app.core.config import settings
from dotenv import load_dotenv

load_dotenv()

client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
DATABASE_URL = os.getenv("DATABASE_URL")

# Global Database Pool
db_pool = None

async def init_db_pool():
    """Initializes the global database pool if it doesn't exist."""
    global db_pool
    if not db_pool:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
    return db_pool

# Backwards compatibility for main.py list_modes
KNOWLEDGE_BASE_CACHE = []

async def load_excel_data():
    """No longer used, using PostgreSQL directly."""
    pass

async def get_available_modes():
    """Fetches unique game modes from the PostgreSQL database."""
    await init_db_pool()
    if not db_pool:
        return ["Mode 1", "Mode 2"]
    async with db_pool.acquire() as conn:
        try:
            rows = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos WHERE gameplay_mode IS NOT NULL")
            return [row['gameplay_mode'] for row in rows]
        except Exception as e:
            print(f"Error fetching modes: {e}")
            return ["Mode 1", "Mode 2"] # Fallback

async def get_embedding(text: str):
    """Generates embedding for the given text using OpenAI."""
    try:
        response = await client.embeddings.create(
            input=[text.replace("\n", " ")],
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Embedding Generation Error: {e}")
        return None

async def semantic_search_database(query: str, limit: int = 3) -> str:
    """Performs a vector search on the database to find semantically relevant card combos."""
    embedding = await get_embedding(query)
    if not embedding:
        return "Error generating embedding for search."
        
    vec_str = "[" + ",".join(map(str, embedding)) + "]"
    
    await init_db_pool()
    if not db_pool:
        return "The knowledge base is currently offline."
    async with db_pool.acquire() as conn:
        try:
            # Semantic search using cosine distance (<=> operator in pgvector)
            # We filter for rows that actually have an embedding
            sql = '''
                SELECT gameplay_mode, character, virtue_karma, validation_reason, valmiki_reference_anchor, kanda,
                       1 - (embedding <=> $1) as similarity
                FROM card_combos
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1
                LIMIT $2
            '''
            rows = await conn.fetch(sql, vec_str, limit)
            
            if not rows:
                return "No semantically similar combos found."
                
            results = []
            for row in rows:
                results.append(
                    f"[Similarity: {row['similarity']:.3f}] Mode: {row['gameplay_mode']}, "
                    f"Combo: {row['character']} + {row['virtue_karma']}\n"
                    f"Reason: {row['validation_reason']}\n"
                    f"Ref: {row['kanda']}, {row['valmiki_reference_anchor']}\n"
                )
            return "\n---\n".join(results)
            
        except Exception as e:
            print(f"Semantic Search Error: {e}")
            return f"Error performing semantic search: {str(e)}"

async def query_postgres_database(mode: str, character: str, karma: str) -> str:
    """Queries the live PostgreSQL database for the specified combo in the given mode."""
    # Ensure mode name matches the format in the DB (e.g., "Mode 1")
    # Handle formats like "mode_1", "mode1", "Mode 1"
    formatted_mode = mode.replace("_", " ").strip()
    if formatted_mode.lower() == "mode1": formatted_mode = "Mode 1"
    elif formatted_mode.lower() == "mode2": formatted_mode = "Mode 2"
    else:
        # Generic "mode X" to "Mode X"
        import re
        match = re.match(r"mode\s*(\d+)", formatted_mode, re.IGNORECASE)
        if match:
            formatted_mode = f"Mode {match.group(1)}"
        else:
            formatted_mode = formatted_mode.title()
    
    await init_db_pool()
    if not db_pool:
        return f"Database lookup failed for {formatted_mode}. Using offline mode."
    async with db_pool.acquire() as conn:
        try:
            # Search by name OR by card number (Order Independent)
            query = '''
                SELECT combo_status, final_status, validation_reason, valmiki_reference_anchor, kanda
                FROM card_combos 
                WHERE LOWER(gameplay_mode) = LOWER($1)
                AND (
                    (
                        (LOWER(character) = LOWER($2) OR CAST(character_card_number AS TEXT) = $2)
                        AND (LOWER(virtue_karma) = LOWER($3) OR CAST(virtue_karma_card_number AS TEXT) = $3)
                    )
                    OR
                    (
                        (LOWER(character) = LOWER($3) OR CAST(character_card_number AS TEXT) = $3)
                        AND (LOWER(virtue_karma) = LOWER($2) OR CAST(virtue_karma_card_number AS TEXT) = $2)
                    )
                )
            '''
            
            row = await conn.fetchrow(query, formatted_mode, character, karma)
            
            if row:
                # Provide full details including reference anchor and kanda for the AI
                return (
                    f"Combo Status: {row['combo_status']} (Final Status: {row['final_status']})\n"
                    f"Validation Reason: {row['validation_reason']}\n"
                    f"Reference: {row['valmiki_reference_anchor']}\n"
                    f"Kanda: {row['kanda']}"
                )
            else:
                return f"No matching combo found in {formatted_mode} for Character: {character} and Virtue: {karma}."
                
        except Exception as e:
            print(f"PostgreSQL Query Error: {e}")
            return f"Error retrieving data from database: {str(e)}"

