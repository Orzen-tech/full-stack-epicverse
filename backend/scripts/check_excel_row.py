import pandas as pd

FILE_PATH = 'e:/kriyora/model_try/backend/data/EpicVerse_Mode_2.xlsx'

def check():
    df = pd.read_excel(FILE_PATH)
    # The actual column names might have spaces or be different
    # Let's find rows where 'Character Card Number' is 1 and 'Virtu/Karma Card Number' is 27
    # Note the spelling from previous check: 'Virtu/Karma Card Number'
    
    match = df[(df['Character Card Number'] == 1) & (df['Virtu/Karma Card Number'] == 27)]
    if not match.empty:
        print("Excel Match Found!")
        row = match.iloc[0]
        for col in df.columns:
            print(f"{col}: {row[col]}")
    else:
        print("No match in Excel for 1 and 27")

if __name__ == '__main__':
    check()
