# EpicVerse Backend API (FastAPI)

FastAPI application providing authentication, user management, OTP services, and AI companion integration for the EpicVerse mobile application.

---

## Security & Architecture (OWASP MASVS Compliance)

### Server-Side Authentication & Authorization (MASTG-NETWORK-1)

Client applications expose API route strings within mobile binaries. To maintain robust defense-in-depth, security is enforced entirely on the server side:

1. **Token & Session Authorization:**
   - Protected API routes enforce server-side validation of Firebase ID tokens or session headers (`Authorization: Bearer <token>`).
   - Unauthenticated requests to protected endpoints return `401 Unauthorized` or `403 Forbidden`.

2. **Invite Code Enforcement:**
   - Registration and OTP request flows enforce valid invite code authorization (`_authorize_otp_request`) to prevent bypasses and unauthorized user onboarding.

3. **Rate Limiting:**
   - Unauthenticated public endpoints (e.g., OTP dispatch) enforce strict rate-limiting (3 requests per 10-minute window per email/IP) returning `429 Too Many Requests`.

4. **Data Sanitization & Logging (MASVS-CODE-4):**
   - Application logs omit PII, plain-text OTPs, tokens, and UIDs to prevent sensitive log exposure in production.

---

## Running Backend Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Start FastAPI development server
uvicorn app.main:app --reload --port 8000
```
