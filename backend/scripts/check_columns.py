import pandas as pd
import os

data_dir = "e:/kriyora/EpicVerse/backend/data"
files = [
    "Mode 2 - CrownShift (Ayodhya Kanda).xlsx",
    "Mode 4 - GlowLine (Kishkindha Kanda).xlsx", 
    "Mode 6 - WarRoom (Yuddha Kanda).xlsx",
    "Mode 7 - Afterlight (Uttara Kanda).xlsx"
]

for file in files:
    path = os.path.join(data_dir, file)
    if os.path.exists(path):
        df = pd.read_excel(path)
        print(f"{file} -> {df.columns.tolist()}")
    else:
        print(f"File NOT FOUND: {file}")
