import pandas as pd
import os

def check_structure(file_path):
    print(f"\nChecking: {file_path}")
    df = pd.read_excel(file_path)
    print(f"Columns: {list(df.columns)}")

if __name__ == "__main__":
    base_path = 'e:/kriyora/model_try/backend/data/'
    for i in range(3, 6):
        file_path = os.path.join(base_path, f'EpicVerse_Mode_{i}.xlsx')
        if os.path.exists(file_path):
            check_structure(file_path)
