import socket
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()
url = urlparse(os.getenv("DATABASE_URL"))
host = url.hostname
port = url.port or 5432

def check_port(host, port):
    print(f"Checking {host}:{port}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect((host, port))
        print("✅ Port is OPEN!")
    except Exception as e:
        print(f"❌ Port is CLOSED or filtered: {e}")
    finally:
        s.close()

if __name__ == "__main__":
    check_port(host, port)
