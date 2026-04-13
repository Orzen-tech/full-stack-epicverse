import pandas as pd
import os

data_dir = "e:/kriyora/EpicVerse/backend/data"
for i in [4, 6]:
    file = f"mode{i}.xlsx"
    path = os.path.join(data_dir, file)
    if os.path.exists(path):
        df = pd.read_excel(path)
        chars = df['Character'].unique()
        print(f"{file} | Characters: {list(chars)[:15]}")
