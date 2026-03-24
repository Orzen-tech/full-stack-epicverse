import pandas as pd
import os

data_dir = "e:/kriyora/model_try/backend/data"
for file in os.listdir(data_dir):
    if file.endswith(".xlsx"):
        df = pd.read_excel(os.path.join(data_dir, file))
        print(f"File: {file}")
        # Find column
        game_mode_col = None
        for col in df.columns:
            if str(col).lower().strip() in ['gameplay mode', 'game mode', 'gamemode', 'mode']:
                game_mode_col = col
                break
        if game_mode_col:
            print(f"Unique values in {game_mode_col}: {df[game_mode_col].unique().tolist()}")
        else:
            print("Gameplay Mode column NOT found")
