import asyncio
import websockets
import json
import time
import urllib.parse
import os
from dotenv import load_dotenv

load_dotenv()

async def run_elite_audit():
    # Use the stable production port
    mode_name = "WarRoom (Yuddha Kanda)"
    mode = urllib.parse.quote(mode_name)
    url = f"ws://localhost:8001/api/v1/ws/realtime?gameMode={mode}"
    
    print(f"\n📡 Connecting to EpicVerse Bridge (8001)...")
    
    try:
        async with websockets.connect(url, ping_interval=None) as ws:
            # Consume initial events (session.created/updated)
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("type") == "session.updated":
                    print(f"✅ Bridge Ready. Mode: {mode_name}")
                    break
            
            # Query Rama (1) + Duty (25)
            # This combo exists in Sarga 24 of Yuddha Kanda according to your spreadsheet.
            query = "Analyze card 1 and 25"
            print(f"🎤 Query: {query}")
            
            start_time = time.perf_counter()
            
            # 1. Inject query
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
            
            # 3. Monitor for results
            while True:
                msg = json.loads(await ws.recv())
                
                # We are looking for the 'response.done' which summarizes the output
                if msg.get("type") == "response.done":
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    print(f"⏱️ RESPONSE LATENCY: {latency_ms:.2f}ms")
                    
                    # Capture tool call and transcript
                    items = msg.get("response", {}).get("output", [])
                    found_citation = False
                    for item in items:
                        if item.get("type") == "message":
                            for part in item.get("content", []):
                                if part.get("type") == "audio_transcription":
                                    txt = part.get("text", "")
                                    if "Sarga 24" in txt or "Yuddha Kanda" in txt:
                                        found_citation = True
                                        print(f"📜 CITATION VERIFIED: {txt}")
                                        break
                                elif part.get("type") == "text":
                                    txt = part.get("text", "")
                                    if "Sarga 24" in txt or "Yuddha Kanda" in txt:
                                        found_citation = True
                                        print(f"📜 TEXT VERIFIED: {txt}")
                                        break
                    
                    if found_citation:
                        print("✅ SUCCESS: Divine Truth correctly retrieved.")
                    else:
                        print("⚠️ WARNING: AI replied but citation not found in text yet.")
                    break
                    
                if msg.get("type") == "error":
                    print(f"❌ SESSION ERROR: {msg}")
                    break

    except Exception as e:
        print(f"❌ Connection Failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_elite_audit())
