import asyncio
import json
import base64
import websockets
import urllib.parse

async def run_scenario(mode, language, combo_text, follow_up_text):
    print(f"\n[SCENARIO] Mode: {mode} | Lang: {language} | Query: {combo_text}")
    
    encoded_mode = urllib.parse.quote(mode)
    # Using the Auth Bypass Token for testing
    token = "epic-stress-test-token"
    uri = f"ws://localhost:8000/api/v1/ws/realtime?gameMode={encoded_mode}&token={token}"
    
    async with websockets.connect(uri) as websocket:
        # 1. Send First Question (Combo)
        await websocket.send(json.dumps({
            "type": "text_query",
            "text": f"(System: Speak in {language}) {combo_text}"
        }))
        
        # Wait for First Response
        print(f"[{language}] Waiting for scholarly response...")
        responses = []
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "response.audio_transcript.done":
                        break
                    if data.get("type") == "response.audio_transcript.delta":
                        responses.append(data["delta"])
            except asyncio.TimeoutError: break
        
        first_resp = "".join(responses)
        print(f"[AI 1]: {first_resp}")

        # 2. Send Second Question (Why?)
        print(f"[{language}] Asking 'Why?'...")
        await websocket.send(json.dumps({
            "type": "text_query",
            "text": follow_up_text
        }))

        responses = []
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "response.audio_transcript.done":
                        break
                    if data.get("type") == "response.audio_transcript.delta":
                        responses.append(data["delta"])
            except asyncio.TimeoutError: break
            
        second_resp = "".join(responses)
        print(f"[AI 2]: {second_resp}")
        return first_resp, second_resp

async def main():
    # Test 1: Balakanda - English
    await run_scenario("OriginArc (Balakanda)", "English", "Check numbers 1 and 29", "Why?")
    
    # Test 2: Sundara Kanda - Tamil
    await run_scenario("lankaLeap (SundaraKanda)", "Tamil", "என்னுடைய 5 மற்றும் 10 எண்களை சரிபார்க்கவும்", "ஏன் சொல்லுங்கள்?")
    
    # Test 3: Yuddha Kanda - Hindi
    await run_scenario("WarRoom (YuddhaKanda)", "Hindi", "12 और 24 का मेल कैसा है?", "इसका अर्थ क्या है?")

if __name__ == "__main__":
    asyncio.run(main())
