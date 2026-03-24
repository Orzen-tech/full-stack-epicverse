import pandas as pd
import os
import sys

# Set stdout to use utf-8 if possible, but simpler to just ignore errors
def safe_print(s):
    print(str(s).encode('ascii', 'ignore').decode('ascii'))

data_dir = "e:/kriyora/model_try/backend/data"
for file in os.listdir(data_dir):
    if file.endswith(".xlsx"):
        df = pd.read_excel(os.path.join(data_dir, file))
        safe_print(f"File: {file}")
        safe_print(f"Columns: {df.columns.tolist()}")
        safe_print(f"Total Rows: {len(df)}")
