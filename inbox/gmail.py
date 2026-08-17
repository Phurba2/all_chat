"""Minimal Gmail integration using Python's stdlib (IMAP fetch, SMTP send).

Configure via environment variables:
    GMAIL_EMAIL        your gmail address
    GMAIL_APP_PASSWORD an app password (https://myaccount.google.com/apppasswords)
"""
import imaplib
import os
import re
import smtplib
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr

from .models import Message

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"


def is_configured():
    return bool(os.environ.get("GMAIL_EMAIL") and os.environ.get("GMAIL_APP_PASSWORD"))


def _decode(value):
    """Decode RFC2047-encoded header text, e.g. '=?utf-8?Q?...' -> plain string."""
    if not value:
        return ""
    return "".join(
        p.decode(charset or "utf-8", "replace") if isinstance(p, bytes) else p
        for p, charset in decode_header(value)
    )


def fetch_emails(limit=25):
    """Pull recent unread emails from the inbox and store them as Message rows.

    Returns the number of new messages created. Already-seen emails (matched by
    Message-ID) are skipped, so it's safe to run repeatedly.
    """
    if not is_configured():
        raise RuntimeError("Set GMAIL_EMAIL and GMAIL_APP_PASSWORD environment variables first.")
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        mail.login(os.environ["GMAIL_EMAIL"], os.environ["GMAIL_APP_PASSWORD"])
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        ids = data[0].split()
        new = 0
        for uid in ids[-limit:]:
            _, msg = mail.fetch(uid, "(BODY.PEEK[])")
            email = message_from_bytes(msg[0][1])
            message_id = _decode(email.get("Message-ID", "")).strip()
            if message_id and Message.objects.filter(message_id=message_id).exists():
                continue
            body = _get_body(email)
            if not body:
                continue
            _, address = parseaddr(_decode(email.get("From", "")))
            name = _decode(email.get("From", "")).split("<")[0].strip(' "')
            Message.objects.create(
                channel="email",
                contact=address or name or "Unknown",
                direction="in",
                subject=_decode(email.get("Subject", ""))[:255],
                text=body,
                message_id=message_id or f"uid-{uid.decode()}",
            )
            new += 1
        return new
    finally:
        mail.logout()


def _get_body(email):
    """Return the plain-text body of an email (falls back to HTML stripped of tags)."""
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


def send_reply(to_address, text, in_reply_to=""):
    """Send an outgoing email reply via SMTP, threaded against the last incoming email."""
    if not is_configured():
        raise RuntimeError("Set GMAIL_EMAIL and GMAIL_APP_PASSWORD environment variables first.")
    last = Message.objects.filter(channel="email", contact=to_address, direction="in").last()
    msg = EmailMessage()
    msg["From"] = os.environ["GMAIL_EMAIL"]
    msg["To"] = to_address
    msg["Subject"] = "Re: " + (last.subject if last and last.subject else "your message")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(text)
    with smtplib.SMTP_SSL(SMTP_HOST, 465) as s:
        s.login(os.environ["GMAIL_EMAIL"], os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)
