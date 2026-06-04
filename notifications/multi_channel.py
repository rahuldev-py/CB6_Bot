# notifications/multi_channel.py — Notification fan-out scaffold
#
# Currently active: Telegram (utils/telegram_alerts.py)
# Scaffolded (need API keys in .env to enable):
#   - Twilio voice/SMS (#25 voice alerts)
#   - SendGrid email
#   - WhatsApp Business
#
# To enable a channel, set the corresponding env var and the dispatcher
# will fan out automatically. No Telegram fallback breakage if Telegram is down.
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger


def send_telegram(message):
    from utils.telegram_alerts import send_message
    return send_message(message)


def send_email(subject, body):
    """SendGrid email — set SENDGRID_API_KEY + ALERT_EMAIL in .env."""
    api_key = os.getenv('SENDGRID_API_KEY')
    to_addr = os.getenv('ALERT_EMAIL')
    if not api_key or not to_addr:
        return False
    try:
        # pip install sendgrid
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        msg = Mail(
            from_email='alerts@cb6quantum.local',
            to_emails=to_addr,
            subject=subject,
            html_content=body
        )
        SendGridAPIClient(api_key).send(msg)
        return True
    except Exception as e:
        logger.error(f"Email error: {e}")
        return False


def send_voice_call(message):
    """Twilio voice call — set TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, ALERT_PHONE."""
    sid   = os.getenv('TWILIO_SID')
    token = os.getenv('TWILIO_TOKEN')
    src   = os.getenv('TWILIO_FROM')
    dst   = os.getenv('ALERT_PHONE')
    if not all([sid, token, src, dst]):
        return False
    try:
        # pip install twilio
        from twilio.rest import Client
        client = Client(sid, token)
        import html as _html
        # TwiML inline — speaks the message (escape to avoid malformed XML)
        twiml = f'<Response><Say voice="alice">{_html.escape(message)}</Say></Response>'
        call = client.calls.create(twiml=twiml, to=dst, from_=src)
        logger.info(f"Voice call placed: {call.sid}")
        return True
    except Exception as e:
        logger.error(f"Voice call error: {e}")
        return False


def send_whatsapp(message):
    """Twilio WhatsApp Business — set TWILIO_WA_FROM + ALERT_WHATSAPP."""
    sid   = os.getenv('TWILIO_SID')
    token = os.getenv('TWILIO_TOKEN')
    src   = os.getenv('TWILIO_WA_FROM')   # 'whatsapp:+14155238886' (sandbox)
    dst   = os.getenv('ALERT_WHATSAPP')
    if not all([sid, token, src, dst]):
        return False
    try:
        from twilio.rest import Client
        Client(sid, token).messages.create(body=message, from_=src, to=dst)
        return True
    except Exception as e:
        logger.error(f"WhatsApp error: {e}")
        return False


def broadcast_alert(message, priority='NORMAL'):
    """
    Fan out a message across all configured channels.
    priority = 'CRITICAL' triggers voice call.
    """
    delivered = []
    if send_telegram(message):
        delivered.append('telegram')
    if send_email("CB6 Alert", message):
        delivered.append('email')
    if send_whatsapp(message):
        delivered.append('whatsapp')
    if priority == 'CRITICAL':
        # Voice for drawdown halts, broker disconnects, large losses
        short = message[:200]
        if send_voice_call(short):
            delivered.append('voice')
    return delivered
