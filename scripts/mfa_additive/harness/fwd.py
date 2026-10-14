"""TCP forwarder: host 127.0.0.1:18081/18082 -> isolated backends. Pure byte forwarding; the backends keep no internet access."""
import asyncio
MAP = {8081: ("mfa-be-L", 8080), 8082: ("mfa-be-M", 8080), 8083: ("mfa-be-A", 8080)}
async def pipe(r, w):
    try:
        while (d := await r.read(65536)): w.write(d); await w.drain()
    except Exception: pass
    finally:
        try: w.close()
        except Exception: pass
def handler(host, port):
    async def h(cr, cw):
        try: ur, uw = await asyncio.open_connection(host, port)
        except Exception: cw.close(); return
        await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
    return h
async def main():
    for lp, (h, p) in MAP.items(): await asyncio.start_server(handler(h, p), "0.0.0.0", lp)
    await asyncio.Event().wait()
asyncio.run(main())
