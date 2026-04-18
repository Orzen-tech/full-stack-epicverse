import hashlib
import json
import asyncio
from typing import Any

import asyncpg
import openai
from redis.asyncio import Redis

from app.core.config import settings

client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

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

KNOWLEDGE_BASE_CACHE: list[dict[str, Any]] = []


async def init_db_pool() -> asyncpg.Pool | None:
    global db_pool, _db_pool_failed
    if _db_pool_failed:
        return None
    if db_pool is None:
        try:
            db_pool = await asyncpg.create_pool(
                settings.ASYNC_DATABASE_URL,
                min_size=1,   # start with 1 connection so timeout=10 is per-pool not per-connection*20
                max_size=settings.DB_POOL_MAX_SIZE,
                max_inactive_connection_lifetime=300,
                command_timeout=settings.DB_COMMAND_TIMEOUT_SECONDS,
                timeout=10,   # fail fast if DB is unreachable
            )
        except Exception as e:
            print(f"[DB] Pool creation failed: {e}")
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
    return None


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
        response = await client.embeddings.create(
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
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            EXACT_LOOKUP_SQL,
            character_card_number,
            attribute_card_no,
            gameplay_mode,
        )

    if row is None:
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


async def resolve_segment_lookup(
    character_card_number: int | None = None,
    attribute_card_no: int | None = None,
    query: str | None = None,
    game_mode: str = "Mode 1 Origin Arc( Balakanda)",
) -> dict[str, Any]:
    if character_card_number is not None and attribute_card_no is not None:
        exact = await get_segment_exact(character_card_number, attribute_card_no, game_mode)
        return {
            "lookup_type": "exact",
            "result": exact,
        }

    if query:
        numbers = _extract_card_numbers(query)
        if numbers is not None:
            exact = await get_segment_exact(numbers[0], numbers[1], game_mode)
            if exact is not None:
                return {
                    "lookup_type": "exact",
                    "result": exact,
                }

        semantic = await semantic_search_segments(query)
        return {
            "lookup_type": "semantic",
            "result": semantic,
        }

    return {
        "lookup_type": "none",
        "result": None,
    }


async def semantic_search_database(query: str, limit: int = 3) -> str:
    results = await semantic_search_segments(query=query, limit=limit)
    if not results:
        return "No semantically similar combos found."
    return "\n---\n".join(
        [
            (
                f"[Similarity: {item['similarity']:.3f}] "
                f"{item['character_card_number']} + {item['attribute_card_no']}\n"
                f"{item.get('final_status') or ''}\n"
                f"{item.get('revised_scholar_reason') or item.get('final_segment') or ''}"
            ).strip()
            for item in results
        ]
    )


async def query_postgres_database(mode: str, character: str, karma: str) -> str:
    try:
        c_num = int(character)
        k_num = int(karma)
        
        # LOGIC RULE: Both numbers are character cards (1 to 24)
        if 1 <= c_num <= 24 and 1 <= k_num <= 24:
            return json.dumps({
                "status": "Invalid",
                "final_segment": "Both numbers are character cards, they are not a combo.",
                "revised_scholar_reason": "In the game rules, a combo must consist of one character card (1-24) and one attribute card (25+). Two character cards cannot form a combination."
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
