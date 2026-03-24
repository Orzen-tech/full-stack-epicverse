import pandas as pd

FILE_PATH = 'e:/kriyora/model_try/backend/data/EpicVerse_Mode_2.xlsx'

def check():
    df = pd.read_excel(FILE_PATH)
    match = df[(df['Character Card Number'] == 1) & (df['Virtu/Karma Card Number'] == 27)]
    if not match.empty:
        row = match.iloc[0].to_dict()
        print("COLUMNS AND VALUES:")
        for k, v in row.items():
            print(f"'{k}': {v}")
    else:
        print("NOT FOUND")

if __name__ == '__main__':
    check()
