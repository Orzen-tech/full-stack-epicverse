import pandas as pd
import glob
import os

def run_accuracy_report():
    files = glob.glob('data/mode*.xlsx')
    print(f"\n🔍 Scanning {len(files)} Truth Files...")
    print("-" * 65)

    for f in files:
        try:
            df = pd.read_excel(f)
            # Try to identify the mode name in the first cell or row
            first_val = str(df.iloc[0, 0])
            if 'Yuddha Kanda' in first_val or 'WarRoom' in first_val:
                print(f"🌟 FOUND MATCH: {f} (Mode: {first_val})")
                
                # Test Cases from Image
                test_combos = [
                    (1, 25, "Duty", "Sarga 24"),
                    (1, 26, "Righteousness", "Sarga 103"),
                    (1, 29, "Courage", "Sarga 108"),
                    (1, 31, "Attunement", "Sarga 24")
                ]
                
                for c1, c2, attr, expected in test_combos:
                    # Look for card1 in col index 2 and card2 in col index 5 (based on common template)
                    # We will filter by the literal values
                    row = df[(df.iloc[:, 2] == c1) & (df.iloc[:, 5] == c2)]
                    
                    if not row.empty:
                        status = row.iloc[0, 11] # Final Segment column (usually index 11)
                        reason = row.iloc[0, 12] # Revised Scholar Reason (usually index 12)
                        
                        print(f"\n[COMBO] {c1} & {c2} ({attr})")
                        print(f"  🏛️ Segment Status: {status}")
                        print(f"  📜 Citation Found: {expected in str(reason)}")
                        if expected in str(reason):
                            print(f"  ✅ SUCCESS: Database match found ({expected})")
                        else:
                            print(f"  ⚠️ CITATION MISMATCH IN DB: {reason[:100]}...")
                    else:
                        print(f"  ❌ COMBO {c1} & {c2} NOT FOUND IN {f}")
        except Exception as e:
            print(f"  ❌ Error reading {f}: {e}")

if __name__ == "__main__":
    run_accuracy_report()
