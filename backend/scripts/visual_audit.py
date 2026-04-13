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
            "text": f"{card1} and {card2}"
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
        
        print(f"[Turn 1 Response]: {''.join(responses)}")

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
            
        print(f"[Turn 2 Response]: {''.join(responses)}")

async def main():
    # Test 1: Valid but Excluded (Rama + Support)
    await audit_combo("OriginArc (Balakanda)", "1", "83")
    
    # Test 2: Invalid (Rama + Revelation)
    await audit_combo("OriginArc (Balakanda)", "1", "38")
    
    # Test 3: Valid (Dasharatha + Duty)
    await audit_combo("OriginArc (Balakanda)", "8", "25")

if __name__ == "__main__":
    asyncio.run(main())
