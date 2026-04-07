import asyncio
import websockets
import json

import urllib.parse

async def verify_realtime():
    # We use a test token (or none if the route allows guest)
    mode = urllib.parse.quote("Origin Arc( Balakanda)")
    url = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={mode}"
    
    print(f"Connecting to {url}...")
    try:
        async with websockets.connect(url) as ws:
            print("Connected! Waiting for OpenAI session events...")
            
            # OpenAI typically sends session.created almost immediately
            while True:
                try:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=10)
                    msg = json.loads(raw_msg)
                    
                    event_type = msg.get("type")
                    if event_type:
                        print(f"[EVENT] {event_type}")
                    
                    # Look for session update confirming tools
                    # In our relay, we might need to send a trigger to get started
                    # or wait for the initial session.created from the relay
                    
                    if event_type == "session.updated":
                        tools = msg.get("session", {}).get("tools", [])
                        tool_names = [t["name"] for t in tools]
                        print(f"Tools Registered: {tool_names}")
                        if "query_database_for_combo" in tool_names:
                            print("✅ SUCCESS: Divine Truth Tool found in Realtime Session!")
                        break
                        
                    if event_type == "error":
                        print(f"❌ ERROR: {msg}")
                        break
                        
                except asyncio.TimeoutError:
                    print("Timed out waiting for session events.")
                    break
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(verify_realtime())
