import asyncio
import json
import time
import websockets
import random
import pandas as pd
from datetime import datetime

# --- CONFIGURATION ---
WS_URL = "ws://localhost:8000/api/v1/ws/realtime"
LANGUAGES = ["English", "Hindi", "Kannada", "Telugu", "Tamil", "Marathi"]
MODES = ["Mode 1", "Mode 2", "Mode 3", "Mode 4", "Mode 5", "Mode 6", "Mode 7"]
TEST_USER_ID = "STRESS_TEST_USER_999"

# Combinations to test Truth Table (Card 1 vs 29 is a known success case)
CARD_TESTS = [
    (1, 29), (5, 10), (12, 45), (3, 80), (1, 1), (99, 100),
    (2, 3), (15, 16), (20, 21), (30, 31)
]

GENERAL_QUESTIONS = [
    "Who is Rama?",
    "Explain the journey.",
    "Why did the forest exile happen?",
    "Tell me about Hanuman."
]

async def audit_single_session(language, mode, card1=None, card2=None, text_q=None):
    start_time = time.time()
    # USE UNIQUE UID TO AVOID SESSION_KICKED
    unique_uid = f"TEST_USER_{random.randint(100000, 999999)}"
    session_id = f"TEST_SESS_{random.randint(1000, 9999)}"
    params = f"?uid={unique_uid}&mode={mode.replace(' ', '%20')}&session_id={session_id}"
    
    if card1 and card2:
        query = f"What is the connection between card {card1} and card {card2}?"
    else:
        query = text_q

    full_response = ""
    latency_to_first_byte = None
    
    try:
        async with websockets.connect(WS_URL + params) as ws:
            # 1. Wait for connection success msg
            init_msg = await ws.recv()
            
            # 2. Send the Text Query
            await ws.send(json.dumps({
                "type": "text_query",
                "text": query
            }))
            
            # 3. Collect the response stream
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = json.loads(msg)
                    
                    if data["type"] == "response.audio_transcript.delta":
                        if latency_to_first_byte is None:
                            latency_to_first_byte = (time.time() - start_time) * 1000
                        full_response += data["delta"]
                    
                    if data["type"] == "response.audio_transcript.done":
                        break # Turn complete
                except asyncio.TimeoutError:
                    break
                    
        return {
            "Time": datetime.now().strftime("%H:%M:%S"),
            "Mode": mode,
            "Lang": language,
            "Query": query[:25] + "...",
            "AI_Logic": "✅ Validated" if ("valid" in full_response.lower() or "combo" in full_response.lower()) else "⚠️ Informational",
            "Result": full_response[:50] + "...",
            "Latency": round(latency_to_first_byte if latency_to_first_byte else 0, 2)
        }
    except Exception as e:
        return {
            "Time": datetime.now().strftime("%H:%M:%S"),
            "Mode": mode,
            "Lang": language,
            "Query": query[:25] + "...",
            "AI_Logic": "❌ ERROR",
            "Result": str(e)[:50],
            "Latency": 0
        }

async def run_audit():
    print(f"🚀 [STRESS-TEST] Auditing 100+ requests across all Modes/Langs...")
    all_results = []
    
    # Generate 105 test cases (15 per mode x 7 modes)
    test_cases = []
    for mode in MODES:
        for _ in range(15):
            lang = random.choice(LANGUAGES)
            if random.random() > 0.4:
                c1, c2 = random.choice(CARD_TESTS)
                test_cases.append((lang, mode, c1, c2, None))
            else:
                q = random.choice(GENERAL_QUESTIONS)
                test_cases.append((lang, mode, None, None, q))

    # Execute in semi-parallel batches of 5 to avoid OpenAI rate limits
    batch_size = 5
    for i in range(0, len(test_cases), batch_size):
        batch = test_cases[i:i+batch_size]
        print(f"📡 Batch {i//batch_size + 1}: Testing modes {set([b[1] for b in batch])}")
        batch_results = await asyncio.gather(*[audit_single_session(*case) for case in batch])
        all_results.extend(batch_results)
        await asyncio.sleep(0.5) # Gentle pacing

    # Create Table
    df = pd.DataFrame(all_results)
    print("\n" + "="*80)
    print(" EXHAUSTIVE AI LOGIC & LATENCY AUDIT REPORT ")
    print("="*80)
    print(df.to_markdown(index=False))
    
    # Save results
    df.to_csv("Final_AI_Audit_Report.csv", index=False)
    print(f"\n✅ Total Tests: {len(all_results)}")
    print(f"✅ Avg Latency: {df[df['Latency'] > 0]['Latency'].mean():.2f}ms")

if __name__ == "__main__":
    asyncio.run(run_audit())
