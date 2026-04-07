import subprocess
import time
import sys

def run_chaos_test():
    print("=============================================")
    print("🔥 ULTIMATE CHAOS MONKEY: FULL SYSTEM SIEGE 🔥")
    print("=============================================\n")
    print("Initiating ALL test suites simultaneously in parallel processes...")
    
    start_time = time.time()

    # Launch all scripts at the exact same time
    processes = []
    
    scripts = [
        {"file": "db_health_check.py", "name": "DB Health"},
        {"file": "ultra_deep_test.py", "name": "Deep Validation (Sequential Modes)"},
        {"file": "qa_stress_test.py", "name": "QA Edge Case Tester (SQL Injections)"},
        {"file": "massive_stress_test.py", "name": "Massive Stress Test (Parallel Translations)"},
        {"file": "multi_user_concurrency_test.py", "name": "Multi-User Leak Test (50 UIDs)"}
    ]

    for script in scripts:
        print(f"🚀 Launching -> {script['name']}")
        import os
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        p = subprocess.Popen(
            [sys.executable, script["file"]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env
        )
        processes.append((p, script["name"]))

    print("\n⚔️ SIEGE ACTIVE. Waiting for all 200+ parallel AI requests to settle...\n")

    # Wait for all processes to finish and collect output
    failed = False
    for p, name in processes:
        stdout, stderr = p.communicate()
        
        print(f"--- RESULTS FOR: {name} ---")
        if p.returncode != 0:
            print(f"❌ FAILED with Exit Code {p.returncode}")
            print(f"Error Log:\n{stderr}")
            failed = True
        else:
            print(f"✅ PASSED.")
            
            # Extract latency lines or success strings if present
            for line in stdout.split('\n'):
                if "LATENCY" in line or "Success Rate" in line or "Total Isolated" in line or '(Avg)' in line or "Zero Session Leakage" in line:
                    print(f"   > {line.strip()}")
        print("-" * 40 + "\n")

    end_time = time.time()
    
    print("=============================================")
    if failed:
        print("💥 SYSTEM COMPROMISED: Some tests failed under pressure.")
    else:
        print("🏆 SYSTEM INVINCIBLE: EpicVerse Survived the Chaos Monkey!")
    
    print(f"Total Framework Siege Time: {end_time - start_time:.2f} seconds")
    print("=============================================")

if __name__ == "__main__":
    run_chaos_test()
