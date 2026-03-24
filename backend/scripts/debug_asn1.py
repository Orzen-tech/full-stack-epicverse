import base64
import json
from pyasn1.codec.der import decoder
from pyasn1.type import univ

with open('e:/kriyora/model_try/backend/google-credentials.json', 'r') as f:
    data = json.load(f)

key_str = data['private_key']
content = key_str.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "").strip()
content = "".join(content.split())
decoded = base64.b64decode(content)

print(f"DER Length: {len(decoded)}")

# Simple hex dump of first 64 bytes
print(f"Hex: {decoded[:64].hex()}")

# Try to decode with pyasn1 if available
try:
    from pyasn1.codec.der import decoder
    substrate = decoded
    while substrate:
        asn1_node, substrate = decoder.decode(substrate)
        print(f"ASN.1 Node: {asn1_node}")
except Exception as e:
    print(f"ASN.1 Error: {e}")
