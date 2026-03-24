import base64
import json

with open('e:/kriyora/model_try/backend/google-credentials.json', 'r') as f:
    data = json.load(f)

key_str = data['private_key']
content = key_str.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "").strip()
content = "".join(content.split())

try:
    decoded = base64.b64decode(content)
    print(f"Decoded successfully! Length: {len(decoded)}")
    # Print first few bytes
    print(f"Bytes: {decoded[:16].hex()}")
except Exception as e:
    print(f"Base64 Decode Error: {e}")
