import asyncio
import json
import websockets
import urllib.parse

async def audit_combo(mode, card1, card2, follow_up="Why?"):
    print(f"\n[AUDIT] Mode: {mode} | Combo: {card1} & {card2}")
    
    encoded_mode = urllib.parse.quote(mode)
    token = "epic-stress-test-token"
    uri = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={encoded_mode}&token={token}"
    
    async with websockets.connect(uri) as websocket:
        # Question 1: The Combo
        await websocket.send(json.dumps({
            "type": "text_query",
            "text": f"(System: English only) {card1} and {card2}"
        }))
        
        responses = []
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "response.audio_transcript.done": break
                    if data.get("type") == "response.audio_transcript.delta":
                        responses.append(data["delta"])
            except asyncio.TimeoutError: break
        
        print(f"[Turn 1]: {''.join(responses)}")

        # Question 2: The Reason
        await websocket.send(json.dumps({
            "type": "text_query",
            "text": follow_up
        }))

        responses = []
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=8.0)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "response.audio_transcript.done": break
                    if data.get("type") == "response.audio_transcript.delta":
                        responses.append(data["delta"])
            except asyncio.TimeoutError: break
            
        print(f"[Turn 2]: {''.join(responses)}")

async def main():
    # DB mapping for WildRun: WildRun (AranyaKanda)
    mode = "WildRun (AranyaKanda)"
    
    # Test 1: Valid (Marichan + Selfishness)
    await audit_combo(mode, "23", "89")
    
    # Test 2: Valid (Marichan + Deception)
    await audit_combo(mode, "23", "91")
    
    # Test 3: Valid but Excluded (Marichan + Pushpaka Vimana)
    await audit_combo(mode, "23", "104")

if __name__ == "__main__":
    asyncio.run(main())
