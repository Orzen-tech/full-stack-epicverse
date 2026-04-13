import pandas as pd
import os

data_dir = "e:/kriyora/EpicVerse/backend/data"
for f in os.listdir(data_dir):
    if f.endswith(".xlsx"):
        path = os.path.join(data_dir, f)
        try:
            df = pd.read_excel(path)
            if df.iloc[:, 0].astype(str).str.contains("Yuddha", case=False).any():
                print(f"FOUND 'Yuddha' in {f} FIRST COLUMN")
        except: pass
