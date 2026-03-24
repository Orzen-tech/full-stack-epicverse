import pandas as pd
import numpy as np
import os

def check_structure(file_path):
    print(f"\nChecking: {file_path}")
    df = pd.read_excel(file_path)
    print(f"Columns: {list(df.columns)}")
    print(f"Sample row: {df.iloc[0].to_dict()}")

if __name__ == "__main__":
    base_path = 'e:/kriyora/model_try/backend/data/'
    for i in range(3, 6):
        file = f'EpicVerse_Mode_{i}.xlsx'
        check_structure(os.path.join(base_path, file))
