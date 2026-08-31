import imaplib, os, re, smtplib
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr

from .models import Message
from ai_summary.services import summarize_message

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"


def is_configured():
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


def fetch_emails():
    if not is_configured():
        raise RuntimeError("Set Email and Password first.")

    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        mail.login(os.environ["GMAIL_EMAIL"], os.environ["GMAIL_APP_PASSWORD"])
        mail.select("INBOX")
        _, data = mail.search(None, "ALL")
        ids = data[0].split()[-3:]
        new = 0

        for uid in ids:
            _, msg = mail.fetch(uid, "(BODY[])")
            email = message_from_bytes(msg[0][1])
            message_id = _decode(email.get("Message-ID", "")).strip()

            if message_id and Message.objects.filter(message_id=message_id).exists():
                continue

            body = _get_body(email)

            summary = summarize_message(body)
            
            name, address = parseaddr(_decode(email.get("From", "")))

            Message.objects.create(
                channel="email",
                contact=address,
                direction="in",
                subject=_decode(email.get("Subject", "")),
                text=body,
                message_id=message_id,
                summary=summary,
            )
            new += 1

        return new
    finally:
        mail.logout()