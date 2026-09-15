import html as html_lib
import imaplib
import os
import re
import smtplib
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr

from .models import Message
from ai_summary.services import summarize_message

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"


def _credentials():
    return os.environ.get("GMAIL_EMAIL"), os.environ.get("GMAIL_APP_PASSWORD")


def is_configured():
    email, password = _credentials()
    return bool(email and password)


def _decode(value):
    if not value:
        return ""
    return "".join(
        p.decode(charset or "utf-8", "replace") if isinstance(p, bytes) else p
        for p, charset in decode_header(value)
    )


def _get_body(email):
    if email.is_multipart():
        for part in email.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "replace").strip()
        for part in email.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                html_body = payload.decode(part.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", "", html_lib.unescape(html_body)).strip()
        return ""
    payload = email.get_payload(decode=True) or b""
    return payload.decode(email.get_content_charset() or "utf-8", "replace").strip()


def fetch_emails():
    if not is_configured():
        raise RuntimeError("Set GMAIL_EMAIL and GMAIL_APP_PASSWORD first.")

    email, password = _credentials()
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        mail.login(email, password)
        mail.select("INBOX")
        _, data = mail.search(None, "ALL")
        all_ids = data[0].split()
        new = 0

        for uid in reversed(all_ids[-50:]):
            if new >= 10:
                break

            _, msg = mail.fetch(uid, "(BODY[])")
            email_msg = message_from_bytes(msg[0][1])
            message_id = _decode(email_msg.get("Message-ID", "")).strip()

            if message_id and Message.objects.filter(message_id=message_id, user_email=email).exists():
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
                user_email=email,
            )
            new += 1

        return new
    finally:
        mail.logout()


def send_reply(to, body, subject="", in_reply_to=None):
    if not is_configured():
        raise RuntimeError("Email credentials not configured.")

    email, password = _credentials()
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
