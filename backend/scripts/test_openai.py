import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

try:
    response = client.models.list()
    print("SUCCESS: OpenAI API Key is valid!")
except Exception as e:
    print(f"ERROR OpenAI: {e}")
