from google.oauth2 import service_account
from google.cloud import speech_v2
import json
import os

with open('e:/kriyora/model_try/backend/google-credentials.json', 'r') as f:
    info = json.load(f)

try:
    print("Testing credentials.from_service_account_info...")
    creds = service_account.Credentials.from_service_account_info(info)
    print("SUCCESS: Credentials object created.")
    
    print("Testing SpeechClient(credentials=creds)...")
    client = speech_v2.SpeechClient(credentials=creds)
    print("SUCCESS: SpeechClient initialized.")
    
except Exception as e:
    print(f"FAILED: {e}")
    import traceback
    traceback.print_exc()
