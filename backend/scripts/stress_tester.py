import asyncio
import json
import uuid
import random
import time
import websockets
import urllib.parse

# Configuration
WS_URL = "ws://127.0.0.1:8000/api/v1/ws/realtime"
TOTAL_USERS = 120
CONCURRENT_USERS = 40  # Spawn in waves to avoid local socket exhaustion
TEST_TOKEN = "epic-stress-test-token"

MODES = [
    "Mode 1 Origin Arc( Balakanda)",
    "Mode 2 CrownShift( Ayodhya Kanda)",
    "Mode 3 WildRun( AranyaKanda)",
    "Mode 4 GlowLine( Kishkindha Kanda)",
    "Mode 5 LankaLeap( SundaraKanda)",
    "Mode 6 WarRoom( Yuddha Kanda)",
    "Mode 7 Afterlight( Uttara Kanda)"
]

LANGUAGES = ["English", "Hindi", "Sanskrit", "Kannada", "Telugu", "Tamil"]

MESSAGES = [
    "Tell me about Rama's character",
    "What is the importance of Dharma in this mode?",
    "Explain the combination of card 10 and 15",
    "How does Hanuman help in this kanda?",
    "Show me the status of the battle"
]

class StressTester:
    def __init__(self):
        self.results = []
        self.start_time = 0

    async def simulate_user(self, user_id):
        mode = random.choice(MODES)
        lang = random.choice(LANGUAGES)
        session_id = str(uuid.uuid4())
        
        # FIX: CORRECTLY ENCODE QUERY PARAMETERS TO AVOID HTTP 400
        params = {
            "gameMode": mode,
            "token": TEST_TOKEN,
            "sessionId": session_id
        }
        query_string = urllib.parse.urlencode(params)
        uri = f"{WS_URL}?{query_string}"
        
        try:
            async with websockets.connect(uri) as websocket:
                # 1. Measure Connection Speed
                conn_start = time.time()
                # Wait for initial greeting if applicable or send first message
                message = random.choice(MESSAGES) + f" (Language: {lang})"
                
                send_start = time.time()
                await websocket.send(json.dumps({
                    "type": "text",
                    "content": message
                }))
                
                # 2. Wait for response
                response = await websocket.recv()
                latency = time.time() - send_start
                
                self.results.append({
                    "user": user_id,
                    "mode": mode,
                    "lang": lang,
                    "latency": latency,
                    "status": "Success"
                })
                # print(f"[User {user_id}] Success | Mode: {mode[:10]}... | Latency: {latency:.2f}s")
                
        except Exception as e:
            self.results.append({
                "user": user_id,
                "status": "Failed",
                "error": str(e)
            })
            if user_id == 0:  # Only print first failure for clarity
                print(f"[DEBUG] User 0 Connection Error: {e}")

    async def run_test(self):
        print(f"🚀 Starting EpicVerse Stress Test: {TOTAL_USERS} Users across {len(MODES)} modes...")
        self.start_time = time.time()
        
        tasks = []
        # Run in waves to be realistic and safe for local port limits
        for i in range(0, TOTAL_USERS, CONCURRENT_USERS):
            wave = [self.simulate_user(j) for j in range(i, i + CONCURRENT_USERS)]
            await asyncio.gather(*wave)
            await asyncio.sleep(1) # Gap between waves
            
        total_duration = time.time() - self.start_time
        self.print_report(total_duration)

    def print_report(self, duration):
        successes = [r for r in self.results if r["status"] == "Success"]
        failures = [r for r in self.results if r["status"] == "Failed"]
        latencies = [r["latency"] for r in successes]
        
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        
        print("\n" + "="*50)
        print("EPICVERSE SCALABILITY REPORT")
        print("="*50)
        print(f"Total Users Simulated: {TOTAL_USERS}")
        print(f"Successful Connections: {len(successes)}")
        print(f"Failed Connections:     {len(failures)}")
        print(f"Total Test Duration:    {duration:.2f} seconds")
        print(f"Average Response Time:  {avg_latency:.2f} seconds")
        print("-" * 50)
        print(f"Modes Tested: {len(MODES)}")
        print(f"Languages Simulated: {len(LANGUAGES)}")
        print("="*50)

if __name__ == "__main__":
    tester = StressTester()
    asyncio.run(tester.run_test())
