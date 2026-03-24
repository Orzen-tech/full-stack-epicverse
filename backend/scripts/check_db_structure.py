import asyncio
import asyncpg
import os
import json
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def check_tables():
    conn = await asyncpg.connect(DATABASE_URL)
    results = {}
    try:
        tables = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        for table in tables:
            t_name = table['table_name']
            columns = await conn.fetch(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{t_name}'")
            rows = await conn.fetch(f"SELECT * FROM {t_name} LIMIT 5")
            
            results[t_name] = {
                "columns": [{"name": c['column_name'], "type": c['data_type']} for c in columns],
                "samples": [dict(r) for r in rows if 'created_at' not in r] # Exclude timestamp for easier viewing
            }
            # Handle some types that are not JSON serializable if needed
            for sample in results[t_name]["samples"]:
                for k, v in sample.items():
                    if hasattr(v, 'isoformat'):
                        sample[k] = v.isoformat()
            
        with open("db_structure.json", "w") as f:
            json.dump(results, f, indent=2)
            
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(check_tables())
