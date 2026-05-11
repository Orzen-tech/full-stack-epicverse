import httpx
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

async def send_feedback_notification(display_name: str, user_email: str, message: str) -> bool:
    """Notifies tech@kriyora.com when a user submits feedback."""
    if not settings.SENDGRID_API_KEY:
        return False
    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {settings.SENDGRID_API_KEY.strip()}",
        "Content-Type": "application/json",
    }
    payload = {
        "personalizations": [{"to": [{"email": "tech@kriyora.com"}],
                               "subject": f"New EpicVerse Feedback from {display_name}"}],
        "from": {"email": settings.SENDGRID_FROM_EMAIL, "name": "EpicVerse AI"},
        "content": [{"type": "text/html", "value": f"""
            <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:24px;
                        border:1px solid #3D1E6B;border-radius:12px;background:#1B0C2D;color:#E8E0F0;">
                <h2 style="color:#C084FC;margin-bottom:4px;">New Feedback Received</h2>
                <p style="color:#9B7DC4;font-size:13px;margin-top:0;">EpicVerse App</p>
                <table style="width:100%;border-collapse:collapse;margin:20px 0;">
                    <tr><td style="padding:8px 0;color:#9B7DC4;width:120px;">From</td>
                        <td style="padding:8px 0;color:#F3E8FF;">{display_name}</td></tr>
                    <tr><td style="padding:8px 0;color:#9B7DC4;">Email</td>
                        <td style="padding:8px 0;color:#F3E8FF;">{user_email}</td></tr>
                </table>
                <div style="background:#2A1245;border-left:3px solid #6D28D9;border-radius:4px;
                            padding:16px;font-size:15px;color:#E8E0F0;line-height:1.6;">
                    {message}
                </div>
            </div>
        """}],
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                print(f"[SENDGRID-ERROR] Feedback notify failed: {response.status_code}")
                return False
            print(f"[SENDGRID-SUCCESS] Feedback notification sent for {display_name}")
            return True
    except Exception as e:
        print(f"[SENDGRID-FATAL] Feedback notify error: {e}")
        return False


async def send_otp_email(email: str, otp: str) -> bool:
    """Sends a 6-digit OTP to the user's email using SendGrid API."""
    if not settings.SENDGRID_API_KEY:
        logger.error("[EMAIL] CRITICAL: SendGrid API_KEY is missing.")
        return False

    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {settings.SENDGRID_API_KEY.strip()}",
        "Content-Type": "application/json"
    }

    payload = {
        "personalizations": [{
            "to": [{"email": email}],
            "subject": "Your EpicVerse Verification Code"
        }],
        "from": {"email": settings.SENDGRID_FROM_EMAIL, "name": "EpicVerse AI"},
        "content": [{
            "type": "text/html",
            "value": f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
                    <h2 style="color: #D4AF37; text-align: center;">Welcome to EpicVerse</h2>
                    <p>Hello,</p>
                    <p>Use the following 6-digit code to verify your account. This code is valid for 1 minute.</p>
                    <div style="background: #fdf6e4; padding: 20px; text-align: center; font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #333; border-radius: 5px; border: 1px dashed #D4AF37;">
                        {otp}
                    </div>
                    <p style="margin-top: 20px;">If you did not request this code, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="font-size: 12px; color: #999; text-align: center;">EpicVerse AI &bull; Verification Service</p>
                </div>
            """
        }]
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                print(f"[SENDGRID-ERROR] Status {response.status_code}: {response.text}")
                return False
            print(f"[SENDGRID-SUCCESS] OTP sent to {email}")
            return True
    except Exception as e:
        print(f"[SENDGRID-FATAL] Error sending to {email}: {e}")
        return False


async def send_password_reset_email(email: str, reset_link: str) -> bool:
    """Sends a password reset link via SendGrid (better deliverability than Firebase default)."""
    if not settings.SENDGRID_API_KEY:
        logger.error("[EMAIL] CRITICAL: SendGrid API_KEY is missing.")
        return False

    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {settings.SENDGRID_API_KEY.strip()}",
        "Content-Type": "application/json"
    }

    payload = {
        "personalizations": [{
            "to": [{"email": email}],
            "subject": "Reset Your EpicVerse Password"
        }],
        "from": {"email": settings.SENDGRID_FROM_EMAIL, "name": "EpicVerse AI"},
        "content": [{
            "type": "text/html",
            "value": f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
                    <h2 style="color: #D4AF37; text-align: center;">Reset Your Password</h2>
                    <p>Hello,</p>
                    <p>We received a request to reset your EpicVerse password. Click the button below to set a new password. This link expires in 1 hour.</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{reset_link}" style="background: #D4AF37; color: #000; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 16px;">
                            Reset Password
                        </a>
                    </div>
                    <p>If the button doesn't work, copy and paste this link into your browser:</p>
                    <p style="word-break: break-all; color: #555; font-size: 13px;">{reset_link}</p>
                    <p style="margin-top: 20px;">If you did not request a password reset, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="font-size: 12px; color: #999; text-align: center;">EpicVerse AI &bull; Account Security</p>
                </div>
            """
        }]
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                print(f"[SENDGRID-ERROR] Password reset email failed: {response.status_code}: {response.text}")
                return False
            print(f"[SENDGRID-SUCCESS] Password reset email sent to {email}")
            return True
    except Exception as e:
        print(f"[SENDGRID-FATAL] Password reset email error: {e}")
        return False
