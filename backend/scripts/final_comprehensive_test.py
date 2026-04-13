import asyncio
import json
import websockets
import urllib.parse

async def test_session(mode, card1, card2, query_text):
    print(f"\n>>> SWITCHING TO MODE: {mode}")
    encoded_mode = urllib.parse.quote(mode)
    token = "epic-stress-test-token"
    uri = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={encoded_mode}&token={token}"
    
    async with websockets.connect(uri) as websocket:
        # Step 1: Speak the Combo
        await websocket.send(json.dumps({
            "type": "text_query",
            "text": query_text
        }))
        
        responses_1 = []
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "response.audio_transcript.done": break
                    if data.get("type") == "response.audio_transcript.delta":
                        responses_1.append(data["delta"])
            except asyncio.TimeoutError: break
        
        print(f"[AI Turn 1]: {''.join(responses_1)}")

        # Step 2: Ask Why
        why_query = "Why?"
        if "मेल" in query_text: why_query = "क्यों?"
        if "தொடர்பு" in query_text: why_query = "ஏன்?"
        
        await websocket.send(json.dumps({
            "type": "text_query",
            "text": why_query
        }))

        responses_2 = []
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "response.audio_transcript.done": break
                    if data.get("type") == "response.audio_transcript.delta":
                        responses_2.append(data["delta"])
            except asyncio.TimeoutError: break
            
        print(f"[AI Turn 2]: {''.join(responses_2)}")

async def main():
    trials = [
        ("OriginArc (Balakanda)", "1", "83", "What is the combo of 1 and 83?"),
        ("CrownShift (AyodhyaKanda)", "8", "25", "8 और 25 का मेल क्या है?"),
        ("WildRun (AranyaKanda)", "23", "91", "23 மற்றும் 91 தொடர்பு எப்படி?"),
        ("GlowLine (KishkindhaKanda)", "1", "81", "Tell me about 1 and 81."),
        ("lankaLeap (SundaraKanda)", "11", "85", "11 और 85 का मेल?"),
        ("WarRoom (YuddhaKanda)", "3", "44", "3 மற்றும் 44 என்ன தொடர்பு?"),
        ("AfterLight (UttaraKanda)", "5", "83", "Is 5 and 83 valid?")
    ]
    
    for mode, c1, c2, q in trials:
        await test_session(mode, c1, c2, q)

if __name__ == "__main__":
    asyncio.run(main())
