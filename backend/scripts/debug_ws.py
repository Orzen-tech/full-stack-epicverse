import asyncio
import json
import websockets
import urllib.parse
import time

async def test():
    mode = 'Origin Arc( Balakanda)'
    uri = f'ws://localhost:8001/api/v1/ws/realtime?gameMode={urllib.parse.quote(mode)}&token=epic-stress-test-token'
    print(f"Connecting to {uri}")
    try:
        async with websockets.connect(uri) as ws:
            conn_msg = await ws.recv()
            print(f"RECV: {conn_msg}")
            
            start = time.time()
            await ws.send(json.dumps({'type': 'text_query', 'text': 'Check combo 1 and 29'}))
            
            while True:
                try:
                    m = await asyncio.wait_for(ws.recv(), timeout=30)
                    e = json.loads(m)
                    print(f"EVENT: {e['type']}")
                    
                    if e['type'] == 'response.audio_transcript.delta':
                        print(f"DELTA: {e['delta']}")
                    
                    if e['type'] == 'response.done':
                        print("Response Done.")
                        # If there was a tool call, we expect another response cycle
                        # But for this simple test, we'll wait a bit more or break
                        # In the real app, the server sends second response auto
                        pass
                    
                    if time.time() - start > 45:
                        print("Test timeout.")
                        break
                        
                except asyncio.TimeoutError:
                    print("Timeout waiting for event.")
                    break
    except Exception as ex:
        print(f"Connection Error: {ex}")

asyncio.run(test())
