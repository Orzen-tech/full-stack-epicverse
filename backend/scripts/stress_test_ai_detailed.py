import asyncio
import json
import time
import websockets
import random
import pandas as pd
from datetime import datetime

# --- CONFIGURATION ---
WS_URL = "ws://localhost:8000/api/v1/ws/realtime"
LANGUAGES = ["English", "Hindi", "Telugu"]
MODES = ["Mode 1", "Mode 2", "Mode 5"]
CARD_PAIRS = [(1, 29), (5, 10), (12, 110), (3, 24)]

async def run_detailed_audit_turn():
    results = []
    print("🚀 [DEEP-AUDIT] Starting 2-Turn Logic Check (Fact -> Why?)...")

    for mode in MODES:
        for lang in LANGUAGES:
            pair = random.choice(CARD_PAIRS)
            print(f"📡 Testing {mode} in {lang} with pair {pair}...")
            
            unique_uid = f"AUDIT_USER_{random.randint(1000,9999)}"
            session_id = f"AUDIT_SESS_{random.randint(1000,9999)}"
            params = f"?uid={unique_uid}&mode={mode.replace(' ', '%20')}&session_id={session_id}"
            
            try:
                async with websockets.connect(WS_URL + params) as ws:
                    await ws.recv() # Connection Success
                    
                    # --- TURN 1: THE FACT ---
                    q1 = f"Are card {pair[0]} and card {pair[1]} a combo?"
                    start_t1 = time.time()
                    await ws.send(json.dumps({"type": "text_query", "text": q1}))
                    
                    resp1 = ""
                    tool_start = 0
                    tool_end = 0
                    
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=20.0)
                        if isinstance(msg, bytes):
                            continue
                        data = json.loads(msg)
                        if data["type"] == "response.audio_transcript.delta":
                            resp1 += data["delta"]
                            print(f" [AI-STREAMING-1]: {data['delta']}", end="", flush=True)
                        if data["type"] == "response.done":
                            print("\n [TURN-1-DONE]")
                            tool_end = time.time()
                            break
                    
                    await asyncio.sleep(2.0)
                    
                    q2 = "Explain the logic for this combination in detail."
                    print(f" 📝 [USER-2]: {q2}")
                    start_t2 = time.time()
                    await ws.send(json.dumps({"type": "text_query", "text": q2}))
                    
                    resp2 = ""
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=20.0)
                        if isinstance(msg, bytes):
                            continue
                        data = json.loads(msg)
                        if data["type"] == "response.audio_transcript.delta":
                            resp2 += data["delta"]
                            print(f" [AI-STREAMING-2]: {data['delta']}", end="", flush=True)
                        if data["type"] == "response.done":
                            print("\n [TURN-2-DONE]")
                            break
                    
                    results.append({
                        "Mode": mode,
                        "Lang": lang,
                        "Pair": f"{pair[0]} & {pair[1]}",
                        "Turn 1 (Fact)": resp1[:40] + "...",
                        "Tool Time": f"{round((tool_end - start_t1)*1000, 0)}ms",
                        "Turn 2 (Why?)": resp2[:60] + "...",
                        "AI Final Response": resp2
                    })
                    
            except Exception as e:
                print(f"❌ Error during {mode}/{lang}: {e}")
                
    # Create Table
    df = pd.DataFrame(results)
    print("\n" + "="*100)
    print(" 2-TURN DEEP SCHOLARLY AUDIT REPORT ")
    print("="*100)
    # Filter columns for markdown display
    display_df = df[["Mode", "Lang", "Pair", "Turn 1 (Fact)", "Tool Time", "Turn 2 (Why?)"]]
    print(display_df.to_markdown(index=False))
    
    # Save the full response log to a file
    df.to_csv("Detailed_AI_Audit_Log.csv", index=False)
    print(f"\n📄 Full AI response log saved to: Detailed_AI_Audit_Log.csv")

if __name__ == "__main__":
    asyncio.run(run_detailed_audit_turn())
