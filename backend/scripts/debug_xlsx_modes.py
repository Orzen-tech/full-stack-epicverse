import pandas as pd
import os

data_dir = "e:/kriyora/model_try/backend/data"
for file in sorted(os.listdir(data_dir)):
    if file.endswith(".xlsx"):
        path = os.path.join(data_dir, file)
        try:
            df = pd.read_excel(path)
            print(f"--- File: {file} ---")
            game_mode_col = None
            for col in df.columns:
                if str(col).lower().strip() in ['gameplay mode', 'game mode', 'gamemode', 'mode']:
                    game_mode_col = col
                    break
            
            if game_mode_col:
                print(f"Modes: {df[game_mode_col].unique().tolist()}")
            else:
                print("No mode column found.")
        except Exception as e:
            print(f"Error reading {file}: {e}")
