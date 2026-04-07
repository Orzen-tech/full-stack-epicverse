import pandas as pd
import os

data_dir = "e:/kriyora/EpicVerse/backend/data"
files = [f"mode{i}.xlsx" for i in range(1, 8)]

for file in files:
    path = os.path.join(data_dir, file)
    if os.path.exists(path):
        df = pd.read_excel(path, nrows=0)
        print(f"{file}: {df.columns.tolist()}")
    else:
        print(f"{file}: NOT FOUND")
