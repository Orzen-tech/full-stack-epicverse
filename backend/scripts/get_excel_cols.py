import pandas as pd
import json

df = pd.read_excel('e:/kriyora/model_try/backend/data/EpicVerse_Mode_2.xlsx')
print(json.dumps(list(df.columns)))
