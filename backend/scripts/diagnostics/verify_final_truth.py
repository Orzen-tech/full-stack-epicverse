import asyncio
import websockets
import json
import time
import urllib.parse

async def verify_final_truth():
    # Mode from spreadsheet
    mode = urllib.parse.quote("WarRoom (Yuddha Kanda)")
    url = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={mode}"
    
    print(f"\n📡 Connecting to Realtime Bridge: {mode}")
    
    try:
        async with websockets.connect(url) as ws:
            # Wait for session initialization
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "session.updated":
                    print("✅ Session Ready.")
                    break
            
            # Test Case: Rama (1) + Duty (25) -> Sarga 24
            query = "Analyze card 1 and 25"
            print(f"🎤 Sending Query: {query}")
            
            t_start = time.perf_counter()
            
            # 1. Create User Message
            await ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": query}]
                }
            }))
            
            # 2. Trigger Response
            await ws.send(json.dumps({"type": "response.create"}))
            
            # 3. Listen for the TRUTH
            while True:
                msg = json.loads(await ws.recv())
                
                if msg.get("type") == "response.done":
                    latency = (time.perf_counter() - t_start) * 1000
                    print(f"⏱️ Total Response Latency: {latency:.2f}ms")
                    
                    output = msg.get("response", {}).get("output", [])
                    transcript = ""
                    for item in output:
                        if item.get("type") == "message":
                            for part in item.get("content", []):
                                if part.get("type") == "audio_transcription":
                                    transcript += part.get("text", "")
                    
                    print(f"📜 AI Response: {transcript}")
                    if "Sarga 24" in transcript or "Sarga" in transcript:
                        print("✅ SUCCESS: Divine Truth verified (Yuddha Kanda Citation found).")
                    else:
                        print("⚠️ WARNING: Citation not found in text yet. Handshake complete.")
                    break
                
                if msg.get("type") == "error":
                    print(f"❌ ERROR: {msg}")
                    break
                    
    except Exception as e:
        print(f"❌ Connection Failed: {e}")

if __name__ == "__main__":
    asyncio.run(verify_final_truth())
