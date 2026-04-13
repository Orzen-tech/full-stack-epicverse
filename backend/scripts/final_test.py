import asyncio
import json
import websockets
import time

async def test():
    uri = 'ws://127.0.0.1:8888/api/v1/ws/realtime?gameMode=Mode%201&token=epic-stress-test-token'
    async with websockets.connect(uri) as ws:
        await ws.recv()
        await ws.send(json.dumps({'type': 'text_query', 'text': 'Check combo 1 and 29'}))
        while True:
            m = await ws.recv()
            if isinstance(m, bytes): continue
            e = json.loads(m)
            if e['type'] == 'response.audio_transcript.done':
                print(f"RESULT: {e['transcript']}")
                break
asyncio.run(test())
