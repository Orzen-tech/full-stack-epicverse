import asyncio
import json
import websockets
import urllib.parse

async def audit_combo(mode, card1, card2, follow_up="Explain in detail."):
    print(f"\n[AUDIT] Mode: {mode} | Combo: {card1} & {card2}")
    
    encoded_mode = urllib.parse.quote(mode)
    token = "epic-stress-test-token"
    uri = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={encoded_mode}&token={token}"
    
    async with websockets.connect(uri) as websocket:
        # Question 1: The Combo
        # Force English for this internal test log
        await websocket.send(json.dumps({
            "type": "text_query",
            "text": f"(System: Respond only in English) {card1} and {card2}"
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
        
        print(f"[AI Phase 1]: {''.join(responses)}")

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
            
        print(f"[AI Phase 2]: {''.join(responses)}")

async def main():
    # My exact DB mapping for crownshift: CrownShift (AyodhyaKanda)
    mode = "CrownShift (AyodhyaKanda)"
    
    # Test 1: Invalid (Lakshmana + Sanjivani)
    await audit_combo(mode, "3", "103")
    
    # Test 2: Valid (Dasharatha + Duty)
    await audit_combo(mode, "8", "25")
    
    # Test 3: Valid but Excluded (Dasharatha + Righteousness)
    await audit_combo(mode, "8", "26")

if __name__ == "__main__":
    asyncio.run(main())
