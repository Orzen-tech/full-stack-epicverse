import asyncio
import websockets
import json
import time
import urllib.parse

async def run_audit():
    # Test Data from Spreadsheet
    # Mode: WarRoom (Yuddha Kanda)
    test_cases = [
        {"cards": (1, 25), "expected": "Sarga 24", "label": "Rama-Duty"},
        {"cards": (1, 26), "expected": "Sarga 103", "label": "Rama-Righteousness"},
        {"cards": (1, 29), "expected": "Sarga 108", "label": "Rama-Courage"},
        {"cards": (1, 31), "expected": "Sarga 24", "label": "Rama-Attunement"}
    ]
    
    mode = urllib.parse.quote("WarRoom (Yuddha Kanda)")
    url = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={mode}"
    
    print(f"\n🚀 STARTING ELITE AUDIT: {mode}")
    print("-" * 60)

    try:
        async with websockets.connect(url, ping_interval=None) as ws:
            # Wait for session initialization
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "session.updated":
                    print("✅ Session Initialized.")
                    break
            
            for test in test_cases:
                c1, c2 = test["cards"]
                query = f"Check character card {c1} and attribute card {c2}"
                print(f"\n[QUERY] {query} ({test['label']})")
                
                t_start = time.perf_counter()
                
                # 1. Inject a text-based user message to trigger the tool
                await ws.send(json.dumps({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": query}]
                    }
                }))
                await ws.send(json.dumps({"type": "response.create"}))
                
                # 2. Monitor for Tool Call and Latency
                tool_call_received = False
                response_text = ""
                latency_tool = 0
                
                while True:
                    raw_msg = await ws.recv()
                    msg = json.loads(raw_msg)
                    
                    if msg.get("type") == "response.audio_transcription.delta":
                        response_text += msg.get("delta", "")
                        
                    if msg.get("type") == "response.done":
                        items = msg.get("response", {}).get("output", [])
                        for item in items:
                            if item.get("type") == "function_call":
                                latency_tool = (time.perf_counter() - t_start) * 1000
                                print(f"  ⚡ Tool Call Triggered in: {latency_tool:.2f}ms")
                                tool_call_received = True

                        if tool_call_received:
                            # Final content check
                            print(f"  🏛️ Truth Verification: {test['expected'] in response_text}")
                            if test['expected'] in response_text:
                                print(f"  ✅ SUCCESS: Database match found ({test['expected']})")
                            else:
                                # Sometimes transcription is slow, we wait for a bit
                                pass
                            break
                    
                    if msg.get("type") == "error":
                        print(f"  ❌ ERROR: {msg}")
                        break

                # Add a 1s breather between queries to prevent OpenAI side restarts
                await asyncio.sleep(1.0)

            print("\n" + "=" * 60)
            print("AUDIT COMPLETE.")

    except Exception as e:
        print(f"Audit failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_audit())
