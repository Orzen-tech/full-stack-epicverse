import asyncio
import json
import time
import websockets
import urllib.parse

async def audit_stages():
    mode = urllib.parse.quote('Origin Arc( Balakanda)')
    uri = f'ws://localhost:8888/api/v1/ws/realtime?gameMode={mode}&token=epic-stress-test-token'
    async with websockets.connect(uri) as ws:
        await ws.recv() # success
        
        print(f"{'Stage':<30} | {'Time (s)':<10}")
        print("-" * 45)
        
        start = time.time()
        await ws.send(json.dumps({'type': 'text_query', 'text': 'Check combo 1 and 29'}))
        
        tool_call_time = None
        ttfb = None
        
        while True:
            try:
                m = await asyncio.wait_for(ws.recv(), 30)
                if isinstance(m, bytes): continue
                e = json.loads(m)
                
                # Check for events indicating tool call started or finished
                # The client doesn't see the tool call start, but it sees the tool result being RELAYED back?
                # No, the client only sees OpenAI events.
                
                if ttfb is None and e['type'] in ["response.audio_transcript.delta"]:
                    ttfb = time.time() - start
                    print(f"{'Latency to First Word':<30} | {ttfb:.3f}s")
                
                if e['type'] == 'response.done':
                    # Look for function_call in the output if it was the first stage
                    output = e.get('response', {}).get('output', [])
                    for item in output:
                        if item.get('type') == 'function_call':
                            tool_call_time = time.time() - start
                            print(f"{'LLM Tool Decision (Round 1)':<30} | {tool_call_time:.3f}s")
                    
                    # If we already have a transcript, it means this was the final response
                    if any(item.get('type') == 'message' for item in output):
                        final_time = time.time() - start
                        print(f"{'Final Completion':<30} | {final_time:.3f}s")
                        break
            except: break

asyncio.run(audit_stages())
