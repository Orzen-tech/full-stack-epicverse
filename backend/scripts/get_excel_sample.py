import pandas as pd
import json
import numpy as np

df = pd.read_excel('e:/kriyora/model_try/backend/data/EpicVerse_Mode_2.xlsx')
df = df.replace({np.nan: None})
print(json.dumps(df.head(1).to_dict(orient='records'), indent=2))
