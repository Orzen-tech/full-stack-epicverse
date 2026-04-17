import httpx
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

async def send_otp_email(email: str, otp: str):
    """
    Sends a 6-digit OTP to the user's email using SendGrid API.
    """
    if not settings.SENDGRID_API_KEY:
        logger.error(f"[EMAIL] CRITICAL: SendGrid API_KEY is missing.")
        return False

    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "personalizations": [{
            "to": [{"email": email}],
            "subject": f"Your EpicVerse Verification Code: {otp}"
        }],
        "from": {"email": settings.SENDGRID_FROM_EMAIL, "name": "EpicVerse AI"},
        "content": [{
            "type": "text/html",
            "value": f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
                    <h2 style="color: #D4AF37; text-align: center;">Welcome to EpicVerse</h2>
                    <p>Hello,</p>
                    <p>Use the following 6-digit code to verify your account. This code is valid for 10 minutes.</p>
                    <div style="background: #fdf6e4; padding: 20px; text-align: center; font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #333; border-radius: 5px; border: 1px dashed #D4AF37;">
                        {otp}
                    </div>
                    <p style="margin-top: 20px;">If you did not request this code, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="font-size: 12px; color: #999; text-align: center;">EpicVerse AI &bull; Production Ready Auth Service</p>
                </div>
            """
        }]
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                print(f"❌ [SENDGRID-ERROR] Status {response.status_code}: {response.text}")
                logger.error(f"[EMAIL] SendGrid Error: {response.text}")
                return False
            
            print(f"📧 [SENDGRID-SUCCESS] OTP sent to {email}")
            logger.info(f"[EMAIL] OTP sent successfully to {email}")
            return True
    except Exception as e:
        print(f"❌ [SENDGRID-FATAL] Error sending to {email}: {e}")
        logger.error(f"[EMAIL] Failed to send email to {email}: {e}")
        return False
