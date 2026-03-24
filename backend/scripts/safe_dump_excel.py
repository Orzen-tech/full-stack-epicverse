import pandas as pd

FILE_PATH = 'e:/kriyora/model_try/backend/data/EpicVerse_Mode_2.xlsx'

def check():
    df = pd.read_excel(FILE_PATH)
    match = df[(df['Character Card Number'] == 1) & (df['Virtu/Karma Card Number'] == 27)]
    if not match.empty:
        row = match.iloc[0].to_dict()
        with open('e:/kriyora/model_try/backend/safe_dump.txt', 'w', encoding='ascii', errors='ignore') as f:
            for k, v in row.items():
                f.write(f"'{k}': {v}\n")
        print("Written to safe_dump.txt")

if __name__ == '__main__':
    check()
