import hashlib
import json
import asyncio
from typing import Any

import asyncpg
from redis.asyncio import Redis

from app.core.config import settings
from app.services.openai_client import get_openai_client

EXACT_LOOKUP_SQL = """
SELECT
    cc.id,
    cc.gameplay_mode,
    cc.character_card_number,
    cc.attribute_card_no,
    cc.final_segment,
    cc.final_status,
    cc.revised_scholar_reason
FROM card_combos AS cc
WHERE cc.character_card_number = $1
  AND cc.attribute_card_no = $2
  AND cc.gameplay_mode = $3
LIMIT 1
"""


SEMANTIC_SEARCH_SQL = """
SELECT
    cc.id,
    cc.gameplay_mode,
    cc.character_card_number,
    cc.attribute_card_no,
    cc.final_segment,
    cc.final_status,
    cc.revised_scholar_reason,
    1 - (ce.embedding <=> $1::vector) AS similarity
FROM card_embeddings AS ce
JOIN card_combos AS cc
  ON cc.id = ce.combo_id
WHERE 1 - (ce.embedding <=> $1::vector) >= $2
ORDER BY ce.embedding <=> $1::vector
LIMIT $3
"""

SCHEMA_SQL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS card_combos (
        id BIGSERIAL PRIMARY KEY,
        gameplay_mode TEXT,
        character TEXT,
        character_card_number INTEGER NOT NULL,
        attribute TEXT,
        attribute_card_no INTEGER NOT NULL,
        final_segment TEXT NOT NULL,
        final_status TEXT,
        revised_scholar_reason TEXT,
        valmiki_reference_anchor TEXT,
        kanda TEXT,
        shloka TEXT,
        explanation_summarized TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS card_embeddings (
        combo_id BIGINT PRIMARY KEY REFERENCES card_combos(id) ON DELETE CASCADE,
        embedding vector(1536) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_card_combos_lookup
    ON card_combos (character_card_number, attribute_card_no, gameplay_mode)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_card_combos_covering_lookup
    ON card_combos (character_card_number, attribute_card_no)
    INCLUDE (final_segment, final_status, revised_scholar_reason)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_card_embeddings_hnsw_cosine
    ON card_embeddings
    USING hnsw (embedding vector_cosine_ops)
    """,
]

db_pool: asyncpg.Pool | None = None
_db_pool_failed: bool = False  # once True, skip retrying so endpoints return fast
redis_client: Redis | None = None
_REDIS_ENABLED: bool = True

# Global Cache for Dual-Reliability (DB + RAM Fallback)
KNOWLEDGE_BASE_CACHE: dict[str, dict[str, Any]] = {}


async def init_db_pool() -> asyncpg.Pool | None:
    global db_pool, _db_pool_failed
    if _db_pool_failed:
        return None
    if db_pool is None:
        for attempt in range(1, 4):
            try:
                dsn = settings.ASYNC_DATABASE_URL
                # Log safe DSN (hide password)
                safe_dsn = dsn.split('@')[0].split(':')[:-1] + ['****'] + [dsn.split('@')[1]] if '@' in dsn else dsn
                print(f"[DB] Attempt {attempt} to connect with DSN: ...@{''.join(safe_dsn[-1] if isinstance(safe_dsn, list) else safe_dsn)}")
                
                db_pool = await asyncpg.create_pool(
                    dsn,
                    min_size=1,
                    max_size=settings.DB_POOL_MAX_SIZE,
                    max_inactive_connection_lifetime=300,
                    command_timeout=settings.DB_COMMAND_TIMEOUT_SECONDS,
                    timeout=15
                )
                print(f"✅ [DB] Connected successfully on attempt {attempt}.")
                return db_pool
            except Exception as e:
                if attempt < 3:
                    print(f"⚠️ [DB] Connection attempt {attempt} failed: {e}. Retrying in 2s...")
                    await asyncio.sleep(2)
                else:
                    import traceback
                    print(f"❌ [DB] Pool creation failed after {attempt} attempts: {e}")
                    traceback.print_exc()
                    _db_pool_failed = True
                    db_pool = None
    return db_pool


async def close_db_pool() -> None:
    global db_pool, _db_pool_failed
    if db_pool is not None:
        await db_pool.close()
        db_pool = None
    _db_pool_failed = False


async def init_redis() -> Redis | None:
    global redis_client, _REDIS_ENABLED
    if not _REDIS_ENABLED:
        return None
    if redis_client is None and settings.REDIS_URL:
        try:
            redis_client = Redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
                health_check_interval=30,
            )
            # Fast-Fail Check
            await asyncio.wait_for(redis_client.ping(), timeout=2.0)
        except Exception as e:
            if _REDIS_ENABLED:
                print(f"[INFRA] Redis Offline: Disabling Semantic Cache.")
            redis_client = None
            _REDIS_ENABLED = False
    return redis_client


async def close_redis() -> None:
    global redis_client
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None


async def load_excel_data() -> None:
    """
    Initializes the in-memory fallback cache by loading data from all spreadsheets.
    This serves as a 'Safety Net' if the database connection fails.
    """
    global KNOWLEDGE_BASE_CACHE
    import pandas as pd
    import os

    data_dir = os.path.join(os.getcwd(), "data")
    KANDA_MAP = {
        1: "OriginArc (Balakanda)",
        2: "CrownShift (AyodhyaKanda)", 
        3: "WildRun (AranyaKanda)",
        4: "GlowLine (KishkindhaKanda)",
        5: "lankaLeap (SundaraKanda)",
        6: "WarRoom (YuddhaKanda)",
        7: "AfterLight (UttaraKanda)"
    }

    print("[BOOT] Pre-loading RAM Fallback Cache from Excel...")
    
    count = 0
    for i in range(1, 8):
        file = f"mode {i}.xlsx"
        path = os.path.join(data_dir, file)
        mode_label = KANDA_MAP[i]
        
        if not os.path.exists(path):
            continue
            
        try:
            # Use pandas to read just the columns we need for the safety net
            df = pd.read_excel(path).replace({pd.NA: None})
            for _, row in df.iterrows():
                try:
                    c_num = int(row.get('Character Card No.', 0)) if pd.notnull(row.get('Character Card No.')) else 0
                    a_num = int(row.get('Attribute Card No.', 0)) if pd.notnull(row.get('Attribute Card No.')) else 0
                    
                    if c_num == 0 or a_num == 0:
                        continue
                        
                    # Normalize for lookup
                    c_num, a_num = _normalize_card_numbers(c_num, a_num)
                    
                    # Store in flat dictionary for O(1) fallback
                    cache_key = f"{mode_label}:{c_num}:{a_num}"
                    KNOWLEDGE_BASE_CACHE[cache_key] = {
                        "id": count,
                        "gameplay_mode": mode_label,
                        "character_card_number": c_num,
                        "attribute_card_no": a_num,
                        "final_segment": str(row.get('App Message (Crisp)', '')),
                        "final_status": str(row.get('Final Segment', '')),
                        "revised_scholar_reason": str(row.get('App Message (Crisp)', ''))
                    }
                    count += 1
                except:
                    continue
        except Exception as e:
            print(f"[BOOT] Failed to load {file} into RAM cache: {e}")

    print(f"[BOOT] RAM Safety Net Ready: {count} combinations loaded.")


async def ensure_card_search_schema() -> None:
    pool = await init_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Sanitize Duplicates (Keep most recent entry for each combo)
            await conn.execute('''
                DELETE FROM card_combos a USING (
                    SELECT MIN(id) as id, character_card_number, attribute_card_no, gameplay_mode
                    FROM card_combos 
                    GROUP BY character_card_number, attribute_card_no, gameplay_mode 
                    HAVING COUNT(*) > 1
                ) b
                WHERE a.character_card_number = b.character_card_number 
                AND a.attribute_card_no = b.attribute_card_no
                AND a.gameplay_mode = b.gameplay_mode
                AND a.id > b.id
            ''')
            
            for statement in SCHEMA_SQL:
                await conn.execute(statement)

            # Ensure combo_id is a PRIMARY KEY to support ON CONFLICT
            pk_check = await conn.fetchval("""
                SELECT count(*) FROM pg_constraint 
                WHERE conrelid = 'card_embeddings'::regclass AND contype = 'p'
            """)
            if pk_check == 0:
                print("[INFRA] Database Repair: Establishing Primary Key on card_embeddings...")
                await conn.execute("ALTER TABLE card_embeddings ADD PRIMARY KEY (combo_id)")

            await _migrate_embeddings(conn)


async def _migrate_embeddings(conn: asyncpg.Connection) -> None:
    has_embedding_column = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'card_combos'
              AND column_name = 'embedding'
        )
        """
    )
    if not has_embedding_column:
        return

    await conn.execute(
        """
        INSERT INTO card_embeddings (combo_id, embedding)
        SELECT id, embedding
        FROM card_combos
        WHERE embedding IS NOT NULL
        ON CONFLICT (combo_id) DO NOTHING
        """
    )


async def get_available_modes() -> list[str]:
    pool = await init_db_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT gameplay_mode
                FROM card_combos
                WHERE gameplay_mode IS NOT NULL
                ORDER BY gameplay_mode
                """
            )
        return [row["gameplay_mode"] for row in rows]
    except Exception as e:
        print(f"[DB] get_available_modes error: {e}")
        return []


def _cache_key(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"epicverse:{prefix}:{digest}"


async def _cache_get(key: str) -> str | None:
    if not _REDIS_ENABLED:
        return None
    client_instance = await init_redis()
    if client_instance is None:
        return None
    try:
        return await client_instance.get(key)
    except Exception:
        return None


async def _cache_set(key: str, value: Any, ttl: int) -> None:
    if not _REDIS_ENABLED:
        return
    client_instance = await init_redis()
    if client_instance is None:
        return
    try:
        await client_instance.set(key, json.dumps(value), ex=ttl)
    except Exception:
        return


async def get_embedding(text: str) -> list[float] | None:
    try:
        response = await get_openai_client().embeddings.create(
            input=[text.replace("\n", " ").strip()],
            model=settings.EMBEDDING_MODEL,
        )
        return response.data[0].embedding
    except Exception as exc:
        print(f"Embedding Generation Error: {exc}")
        return None


def _normalize_card_numbers(first: int, second: int) -> tuple[int, int]:
    if first <= 24 < second:
        return first, second
    if second <= 24 < first:
        return second, first
    return first, second


def _record_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "gameplay_mode": row["gameplay_mode"],
        "character_card_number": row["character_card_number"],
        "attribute_card_no": row["attribute_card_no"],
        "final_segment": row["final_segment"],
        "final_status": row["final_status"],
        "revised_scholar_reason": row["revised_scholar_reason"],
    }


async def get_segment_exact(character_card_number: int, attribute_card_no: int, gameplay_mode: str) -> dict[str, Any] | None:
    character_card_number, attribute_card_no = _normalize_card_numbers(
        character_card_number,
        attribute_card_no,
    )
    key = _cache_key(
        "exact_lookup",
        {
            "character_card_number": character_card_number,
            "attribute_card_no": attribute_card_no,
            "gameplay_mode": gameplay_mode,
        },
    )
    cached = await _cache_get(key)
    if cached:
        return json.loads(cached)

    pool = await init_db_pool()
    row = None
    
    if pool is not None and not _db_pool_failed:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    EXACT_LOOKUP_SQL,
                    character_card_number,
                    attribute_card_no,
                    gameplay_mode,
                )
        except Exception as e:
            print(f"[DB-ERROR] Exact lookup failed: {e}. Falling back to RAM cache.")
            row = None

    # --- FALLBACK LOGIC ---
    if row is None:
        cache_key = f"{gameplay_mode}:{character_card_number}:{attribute_card_no}"
        fallback_data = KNOWLEDGE_BASE_CACHE.get(cache_key)
        if fallback_data:
            # Return immediately from RAM cache
            await _cache_set(key, fallback_data, settings.REDIS_LOOKUP_TTL_SECONDS)
            return fallback_data
        return None

    payload = _record_to_dict(row)
    await _cache_set(key, payload, settings.REDIS_LOOKUP_TTL_SECONDS)
    return payload


async def semantic_search_segments(
    query: str,
    limit: int = 5,
    similarity_threshold: float = 0.70,
) -> list[dict[str, Any]]:
    key = _cache_key(
        "semantic_lookup",
        {
            "query": query,
            "limit": limit,
            "similarity_threshold": similarity_threshold,
        },
    )
    cached = await _cache_get(key)
    if cached:
        return json.loads(cached)

    embedding = await get_embedding(query)
    if embedding is None:
        return []

    vector = "[" + ",".join(str(value) for value in embedding) + "]"
    pool = await init_db_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            SEMANTIC_SEARCH_SQL,
            vector,
            similarity_threshold,
            limit,
        )

    results = []
    for row in rows:
        item = _record_to_dict(row)
        item["similarity"] = float(row["similarity"])
        results.append(item)

    await _cache_set(key, results, settings.REDIS_SEMANTIC_TTL_SECONDS)
    return results


def _extract_card_numbers(query: str) -> tuple[int, int] | None:
    tokens = []
    current = []
    for char in query:
        if char.isdigit():
            current.append(char)
        elif current:
            tokens.append(int("".join(current)))
            current = []
    if current:
        tokens.append(int("".join(current)))
    if len(tokens) < 2:
        return None
    return _normalize_card_numbers(tokens[0], tokens[1])


# Valid character card numbers per mode — sourced from Excel data files.
# Any character card NOT in this set for the selected mode triggers the "wrong mode" response.
VALID_CHARACTERS_PER_MODE: dict[str, set[int]] = {
    "OriginArc (Balakanda)":        {1, 2, 3, 5, 6, 7, 8, 9, 10, 23, 24},
    "CrownShift (AyodhyaKanda)":    {1, 2, 3, 5, 6, 8, 9, 10, 19, 24},
    "WildRun (AranyaKanda)":        {1, 2, 3, 5, 11, 12, 15, 23},
    "GlowLine (KishkindhaKanda)":   {1, 2, 3, 4, 15, 17, 18, 21},
    "lankaLeap (SundaraKanda)":     {2, 4, 11, 13, 14, 18, 20, 21},
    "WarRoom (YuddhaKanda)":        {1, 3, 4, 11, 13, 14, 18, 20, 21, 22},
    "AfterLight (UttaraKanda)":     {1, 2, 3, 4, 5, 6, 13},
}


async def query_postgres_database(mode: str, character: str, attribute: str) -> str:
    try:
        c_num = int(character)
        k_num = int(attribute)

        # LOGIC RULE: Both numbers are character cards (1–24)
        if 1 <= c_num <= 24 and 1 <= k_num <= 24:
            return json.dumps({
                "status": "Invalid",
                "both_character": True,
                "final_segment": "Both numbers are character cards, they are not a combo.",
                "revised_scholar_reason": "A combo must be one character card (1–24) and one attribute card (25–104). Two character cards cannot form a combination."
            })

        # LOGIC RULE: Both numbers are attribute cards (25–104)
        if 25 <= c_num <= 104 and 25 <= k_num <= 104:
            return json.dumps({
                "status": "Invalid",
                "both_attribute": True,
                "final_segment": "Both numbers are attribute cards, they are not a combo.",
                "revised_scholar_reason": "A combo must be one character card (1–24) and one attribute card (25–104). Two attribute cards cannot form a combination."
            })

        # LOGIC RULE: Character card not present in this mode at all
        # Normalize so c_num is always the character card (1–24)
        char_card = c_num if 1 <= c_num <= 24 else k_num
        mode_key = mode.strip()
        valid_chars = VALID_CHARACTERS_PER_MODE.get(mode_key)
        if valid_chars is not None and char_card not in valid_chars:
            return json.dumps({
                "status": "Invalid",
                "character_not_in_mode": True,
                "final_segment": "Character card not present in this mode.",
                "revised_scholar_reason": "This character has no role in the selected mode."
            })

        exact = await get_segment_exact(c_num, k_num, mode)
    except (TypeError, ValueError):
        exact = None

    if exact is None:
        return json.dumps({
            "status": "Invalid",
            "final_segment": "This combination (character/attribute) is not mentioned in the scriptures for this mode.",
            "revised_scholar_reason": "No scholarly reference exists for this specific combination in the current dataset."
        })
    
    return json.dumps({
        "status": exact.get("final_status", "Unknown"),
        "final_segment": str(exact.get("final_segment") or ""),
        "revised_scholar_reason": str(exact.get("revised_scholar_reason") or "")
    })
