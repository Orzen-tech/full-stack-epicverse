import pandas as pd
import os

data_dir = "e:/kriyora/EpicVerse/backend/data"
for i in range(1, 8):
    file = f"mode{i}.xlsx"
    path = os.path.join(data_dir, file)
    if os.path.exists(path):
        try:
            df = pd.read_excel(path)
            mode_name = df['Game Play Mode'].iloc[0] if 'Game Play Mode' in df.columns else 'N/A'
            print(f"{file} | Mode: {mode_name} | Rows: {len(df)}")
        except Exception as e:
            print(f"{file} | Error: {e}")
    else:
        print(f"{file} | Not found")
