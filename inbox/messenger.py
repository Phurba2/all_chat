import os

import requests

from .models import Message

GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "")
PAGE_ID = os.environ.get("META_PAGE_ID")
PAGE_ACCESS_TOKEN = os.environ.get("META_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN")


def is_configured():
    return bool(
        PAGE_ID
        and PAGE_ACCESS_TOKEN
        and GRAPH_VERSION
    )


def send_message(recipient_id, text):
    """Send a message to a user's Page-scoped ID via Meta's Send API."""
    if not is_configured():
        raise RuntimeError("Messenger is not configured.")

    url = (
        f"https://graph.facebook.com/"
        f"{GRAPH_VERSION}/{PAGE_ID}/messages"
    )

    payload = {
        "recipient": {
            "id": recipient_id
        },
        "message": {
            "text": text
        }
    }

    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=15,
    )

    response.raise_for_status()

    return response.json()


def _get_paged(url, headers, params=None, max_pages=10):
    """GET a Graph API endpoint and follow cursor pagination."""
    items = []
    for _ in range(max_pages):
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json()
        items.extend(payload.get("data", []))
        next_url = (payload.get("paging") or {}).get("next")
        if not next_url:
            break
        url = next_url
        params = None  # paging.next already carries the cursor
    return items


def fetch_messages():
    """Backfill inbound Messenger messages via the Graph API.

    The webhook is the normal receive path (Meta pushes events to
    /messenger/webhook/). This is a fallback for catching up on messages
    delivered while the webhook was down or not yet subscribed: it lists the
    Page's conversations, reads each conversation's messages, and stores any
    inbound text messages not already in the database. Returns the number of
    new messages stored.

    Requires a Page Access Token with permission to read the Page's
    conversations (pages_messaging).
    """
    if not is_configured():
        raise RuntimeError("Messenger is not configured.")

    headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"}
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"

    new = 0
    conversations = _get_paged(f"{base}/{PAGE_ID}/conversations", headers, {"fields": "id"})
    for conv in conversations:
        conv_id = conv.get("id")
        if not conv_id:
            continue
        messages = _get_paged(
            f"{base}/{conv_id}/messages",
            headers,
            {"fields": "id,message,from,created_time"},
        )
        for msg in messages:
            if not msg.get("id") or not msg.get("message"):
                continue  # no text (attachments, reactions, ...)
            sender_id = (msg.get("from") or {}).get("id")
            if not sender_id or str(sender_id) == str(PAGE_ID):
                continue  # our own outbound messages
            if Message.objects.filter(channel="messenger", message_id=msg["id"]).exists():
                continue  # already stored (e.g. webhook already received it)
            Message.objects.create(
                channel="messenger",
                contact=sender_id,
                direction="in",
                subject="",
                text=msg["message"],
                message_id=msg["id"],
                user_email="",  # server-level; visible across sessions
            )
            new += 1
    return new
