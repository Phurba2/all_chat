import imaplib, os, re, smtplib
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr

from .models import Message
from ai_summary.services import summarize_message

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"


def is_configured(email=None, password=None):
    if email and password:
        return True
    return bool(os.environ.get("GMAIL_EMAIL") and os.environ.get("GMAIL_APP_PASSWORD"))


def _decode(value):
    if not value:
        return ""
    return "".join(
        p.decode(charset or "utf-8", "replace") if isinstance(p, bytes) else p
        for p, charset in decode_header(value)  )


def _get_body(email):
    if email.is_multipart():
        for part in email.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in email.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", "", html)
        return ""
    return email.get_payload(decode=True).decode(email.get_content_charset() or "utf-8", "replace")


def fetch_emails(email=None, password=None, user_email=None):
    if not is_configured(email, password):
        raise RuntimeError("Set Email and Password first.")

    # Use provided credentials or fall back to environment variables
    email = email or os.environ.get("GMAIL_EMAIL")
    password = password or os.environ.get("GMAIL_APP_PASSWORD")
    user_email = user_email or email  # Default to current email if not specified

    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        mail.login(email, password)
        mail.select("INBOX")
        _, data = mail.search(None, "ALL")
        all_ids = data[0].split()
        new = 0
        
        # Search backwards from most recent emails until we find 3 new ones
        for uid in reversed(all_ids):
            if new >= 3:  # Stop after finding 3 new emails
                break
                
            _, msg = mail.fetch(uid, "(BODY[])")
            email_msg = message_from_bytes(msg[0][1])
            message_id = _decode(email_msg.get("Message-ID", "")).strip()

            # Skip if already exists in database for this user
            if message_id and Message.objects.filter(message_id=message_id, user_email=user_email).exists():
                continue

            body = _get_body(email_msg)

            summary = summarize_message(body)
            
            name, address = parseaddr(_decode(email_msg.get("From", "")))

            Message.objects.create(
                channel="email",
                contact=address,
                direction="in",
                subject=_decode(email_msg.get("Subject", "")),
                text=body,
                message_id=message_id,
                summary=summary,
                user_email=user_email,  # Store which user this message belongs to
            )
            new += 1

        return new
    finally:
        mail.logout()


def send_reply(to, body, subject="", in_reply_to=None, email=None, password=None):
    """Send an email reply via SMTP."""
    if not is_configured(email, password):
        raise RuntimeError("Email credentials not configured.")

    # Use provided credentials or fall back to environment variables
    email = email or os.environ.get("GMAIL_EMAIL")
    password = password or os.environ.get("GMAIL_APP_PASSWORD")

    msg = EmailMessage()
    msg["From"] = email
    msg["To"] = to
    msg["Subject"] = subject or "Re: Your message"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    with smtplib.SMTP_SSL(SMTP_HOST, 465) as smtp:
        smtp.login(email, password)
        smtp.send_message(msg)