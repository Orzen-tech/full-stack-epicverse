from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin import auth

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Extracts firebase ID token from Authorization header, validates it, and returns user dict."""
    token = credentials.credentials
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        print(f"Firebase token error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid Firebase Auth token")
