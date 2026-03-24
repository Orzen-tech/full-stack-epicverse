import pandas as pd
import numpy as np

FILE_PATH = 'e:/kriyora/model_try/backend/data/EpicVerse_Mode_2.xlsx'

def check_excel():
    df = pd.read_excel(FILE_PATH)
    print("Column names:")
    for i, col in enumerate(df.columns):
        print(f"{i}: {col}")
        
    print("\nFirst row sample:")
    print(df.iloc[0].to_dict())

if __name__ == "__main__":
    check_excel()
