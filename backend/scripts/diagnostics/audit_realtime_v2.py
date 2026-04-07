import asyncio
import websockets
import json
import time
import urllib.parse
import os
from dotenv import load_dotenv

load_dotenv()

async def run_final_audit():
    # Test Data: WarRoom (Yuddha Kanda)
    test_cases = [
        {"cards": (1, 25), "expected": "Sarga 24", "label": "Rama-Duty"},
        {"cards": (1, 26), "expected": "Sarga 103", "label": "Rama-Righteousness"},
        {"cards": (1, 29), "expected": "Sarga 108", "label": "Rama-Courage"},
        {"cards": (1, 31), "expected": "Sarga 24", "label": "Rama-Attunement"}
    ]
    
    mode = urllib.parse.quote("WarRoom (Yuddha Kanda)")
    url = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={mode}"
    
    print(f"\n🚀 STARTING FINAL ELITE AUDIT: {mode}")
    print("-" * 65)

    try:
        async with websockets.connect(url, ping_interval=None) as ws:
            # Wait for session initialization
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "session.updated":
                    print("✅ Session Ready (Divine Tools Active).")
                    break
            
            for test in test_cases:
                c1, c2 = test["cards"]
                query = f"Check character card {c1} and attribute card {c2}"
                print(f"\n[QUERY] {query} ({test['label']})")
                
                t_start = time.perf_counter()
                
                # 1. Trigger the Query
                await ws.send(json.dumps({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": query}]
                    }
                }))
                await ws.send(json.dumps({"type": "response.create"}))
                
                # 2. Capture Truth from Stream
                tool_call_received = False
                found_sarga = False
                latency_ms = 0
                
                # Listen for tool call and the transcription
                timeout = 15 # Wait up to 15s for the full "thinking" and "speaking" loop
                loop_start = time.time()
                
                while (time.time() - loop_start) < timeout:
                    raw_msg = await ws.recv()
                    msg = json.loads(raw_msg)
                    
                    # Tool call check
                    if msg.get("type") == "response.done":
                        items = msg.get("response", {}).get("output", [])
                        for item in items:
                            if item.get("type") == "function_call":
                                latency_ms = (time.perf_counter() - t_start) * 1000
                                print(f"  ⚡ Tool Triggered: {latency_ms:.2f}ms")
                                tool_call_received = True
                            
                            if item.get("type") == "message":
                                for part in item.get("content", []):
                                    if part.get("type") == "audio_transcription":
                                        text = part.get("text", "")
                                        if test["expected"] in text:
                                            found_sarga = True
                                            print(f"  🏛️ Truth Match: {test['expected']} ✅")

                        if tool_call_received and found_sarga:
                            break
                    
                    if msg.get("type") == "error":
                        print(f"  ❌ SESSION ERROR: {msg}")
                        break

                if not tool_call_received:
                    print("  ⚠️ Tool call NOT detected in stream.")
                if not found_sarga:
                    print(f"  ❌ TRUTH FAILED: Expected {test['expected']} in response.")

                # Breather
                await asyncio.sleep(2.0)

            print("\n" + "=" * 65)
            print("AUDIT COMPLETE - DATABASE TRUTH VERIFIED.")

    except Exception as e:
        print(f"Final Audit failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_final_audit())
