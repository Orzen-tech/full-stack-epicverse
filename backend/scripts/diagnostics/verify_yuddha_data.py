import pandas as pd
import os

def final_truth_scan():
    f = 'data/mode1.xlsx'
    print(f"🔍 Reading {f}...")
    
    try:
        df = pd.read_excel(f)
        # Filter for Yuddha Kanda
        yuddha = df[df.iloc[:, 0].str.contains('Yuddha Kanda', na=False)]
        print(f"🌟 Found {len(yuddha)} Yuddha Kanda rows.")
        
        # Combinations to check
        checks = [
            (1, 25, "Duty", "Sarga 24"),
            (1, 26, "Righteousness", "Sarga 103"),
            (1, 29, "Courage", "Sarga 108"),
            (1, 31, "Attunement", "Sarga 24")
        ]
        
        for c1, c2, attr, expected in checks:
            row = yuddha[(yuddha['Character Card No.'] == c1) & (yuddha['Attribute Card No.'] == c2)]
            if not row.empty:
                status = row.iloc[0, 7] # Final Segment
                reason = row.iloc[0, -1] # App Message (Crisp)
                print(f"\n[COMBO] {c1} & {c2} ({attr})")
                print(f"  🏛️ Segment: {status}")
                print(f"  📜 Citation Found: {expected in str(reason)}")
                print(f"  ✅ Truth: {str(reason)[:100]}...")
            else:
                print(f"\n[COMBO] {c1} & {c2} ({attr}) NOT FOUND IN YUDDHA KANDA.")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    final_truth_scan()
