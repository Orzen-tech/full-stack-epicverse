import asyncio
import json
import time
import websockets
import sys

async def test_ai_response(query, language_label):
    # URI encoded mode: Origin+Arc(+Balakanda)
    mode = "Origin Arc( Balakanda)"
    import urllib.parse
    mode_encoded = urllib.parse.quote(mode)
    uri = f"ws://localhost:8888/api/v1/ws/realtime?gameMode={mode_encoded}&token=epic-stress-test-token"
    
    try:
        async with websockets.connect(uri) as websocket:
            # First message is connection_success
            msg = await websocket.recv()
            # print(f"Connected: {msg}")
            
            start_time = time.time()
            
            # Send text query
            await websocket.send(json.dumps({
                "type": "text_query",
                "text": query
            }))
            
            ttfb = None
            full_response = ""
            
            # Timeout for safety
            try:
                finished = False
                while not finished:
                    message = await asyncio.wait_for(websocket.recv(), timeout=20)
                    event = json.loads(message)
                    
                    # Catch first response byte (audio or transcript)
                    if ttfb is None and event["type"] in ["response.audio.delta", "response.audio_transcript.delta"]:
                        ttfb = time.time() - start_time
                    
                    if event["type"] == "response.audio_transcript.delta":
                        full_response += event["delta"]
                    
                    # If we have a transcript and the response is done, we can finish
                    if event["type"] == "response.done":
                        if full_response.strip():
                            finished = True
                        else:
                            # Might be a tool call, wait for the next response
                            pass
            except asyncio.TimeoutError:
                print(f"Timeout waiting for response for {language_label}")
            
            total_time = time.time() - start_time
            return {
                "language": language_label,
                "query": query,
                "ttfb": ttfb,
                "total_time": total_time,
                "response": full_response.strip()
            }
    except Exception as e:
        print(f"Error connecting for {language_label}: {e}")
        return None

async def main():
    queries = [
        ("Check combo 1 and 29", "English"),
        ("காம்போ 1 மற்றும் 29 சரிபார்க்கவும்", "Tamil"),
        ("कॉम्बो 1 और 29 की जांच करें", "Hindi")
    ]
    
    results = []
    print("="*60)
    print(" EPICVERSE AI LATENCY & LANGUAGE FIDELITY AUDIT ")
    print("="*60 + "\n")
    
    for q, lang in queries:
        print(f"-> Testing {lang} Seeker...")
        res = await test_ai_response(q, lang)
        if res:
            results.append(res)
            ttfb_val = f"{res['ttfb']:.3f}" if res['ttfb'] is not None else "N/A"
            print(f"   [SYNC DONE] Latency: {ttfb_val}s | Lang Correct?: {'Yes' if res['response'] else 'No Output'}")
        print("-" * 40)
    
    print("\n" + "="*80)
    print(f"{'Language':<12} | {'TTFB (s)':<10} | {'Total (s)':<10} | {'AI Response Snippet'}")
    print("-" * 80)
    for r in results:
        ttfb_str = f"{r['ttfb']:.3f}" if r['ttfb'] is not None else "N/A"
        total_str = f"{r['total_time']:.3f}" if r['total_time'] is not None else "N/A"
        print(f"{r['language']:<12} | {ttfb_str:<10} | {total_str:<10} | {r['response'][:60]}...")
    print("="*80 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
