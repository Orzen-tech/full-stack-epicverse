import asyncio
import json
import websockets
import urllib.parse

async def test():
    mode = "OriginArc (Balakanda)"
    encoded_mode = urllib.parse.quote(mode)
    token = "epic-stress-test-token"
    uri = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={encoded_mode}&token={token}"
    
    async with websockets.connect(uri) as websocket:
        await websocket.send(json.dumps({
            "type": "text_query",
            "text": "Check combo 1 and 29"
        }))
        
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "response.audio_transcript.done": break
            except asyncio.TimeoutError: break

asyncio.run(test())
