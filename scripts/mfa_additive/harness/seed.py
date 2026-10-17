"""Reset and seed both isolated databases with FAKE users only (example.invalid addresses). Runs inside the isolated network."""
import asyncio, json, urllib.request, asyncpg
USERS = [("fake-verified", "verified@example.invalid", True, False), ("fake-unverified", "unverified@example.invalid", False, False),
         ("fake-mfa", "mfa@example.invalid", True, True)]
NEW = ["fake-new-%d" % i for i in range(1, 9)]          # exist in the fake identity service, not in SQL (signup scenarios)
async def one(db, wait=120):
    c = await asyncpg.connect("postgresql://postgres@mfa-pg/" + db)
    for _ in range(wait):  # the backend creates its schema in the background after it reports ready
        if await c.fetchval("select to_regclass('public.users') is not null and to_regclass('public.invite_codes') is not null"): break
        await asyncio.sleep(1)
    else:
        await c.close(); return None   # no backend runs against this database (the optional newer-backend one)
    tabs = [r[0] for r in await c.fetch("select tablename from pg_tables where schemaname='public'")]
    if tabs: await c.execute("TRUNCATE " + ",".join('"%s"' % t for t in tabs) + " RESTART IDENTITY CASCADE")
    for uid, em, ver, mfa in USERS:
        await c.execute("INSERT INTO users (uid, display_name, email, email_verified, mfa_enabled, created_at) VALUES ($1,'Fake User',$2,$3,$4, now() - interval '40 days')", uid, em, ver, mfa)
    await c.execute("INSERT INTO invite_codes (code, current_uses, max_uses, created_at) VALUES ('EPIC-TEST01', 0, 50, now())")
    n = await c.fetchval("select count(*) from users"); await c.close(); return n
def idstub():
    for uid, em, *_ in USERS + [(u, u + "@example.invalid") for u in NEW]:
        req = urllib.request.Request("http://mfa-idstub:9099/_admin/add", data=json.dumps({"uid": uid, "email": em}).encode(), method="POST")
        urllib.request.urlopen(req, timeout=10).read()
async def main():
    print("seeded users L/A/M/X:", await one("dbl"), await one("dba"), await one("dbm", 3), await one("dbx", 3)); idstub(); print("identity stub ready")
asyncio.run(main())
