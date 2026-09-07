import hashlib
import hmac
import json
import os
from pathlib import Path

import requests

from .models import Message

GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "")
PAGE_ID = os.environ.get("META_PAGE_ID")
PAGE_ACCESS_TOKEN = os.environ.get("META_PAGE_ACCESS_TOKEN")
APP_SECRET = os.environ.get("META_APP_SECRET")
VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN")

# Graph API version used when none is given in env or by the caller.
DEFAULT_GRAPH_VERSION = "v21.0"

# Server-level config saved from /setup/messenger/ so the webhook can run
# without env vars. Stored outside the repo (gitignored).
CONFIG_PATH = Path(__file__).resolve().parent.parent / "messenger_server_config.json"


def _read_server_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_server_config(data):
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def save_server_config(page_id=None, access_token=None, app_secret=None,
                       verify_token=None, graph_version=None):
    """Persist Messenger webhook credentials server-side (like the .env vars).

    Meta calls /messenger/webhook/ without a browser session, so credentials
    entered on the setup page must be stored server-side to verify the
    handshake (verify token) and event payloads (app secret).
    """
    data = _read_server_config()
    values = {
        "META_PAGE_ID": page_id,
        "META_PAGE_ACCESS_TOKEN": access_token,
        "META_APP_SECRET": app_secret,
        "META_VERIFY_TOKEN": verify_token,
        "META_GRAPH_VERSION": graph_version,
    }
    for key, value in values.items():
        if value:
            data[key] = value
    _write_server_config(data)


def _config_value(key, env_fallback):
    """Server config saved from the setup page, falling back to env vars."""
    return _read_server_config().get(key) or env_fallback


def webhook_page_id():
    """Page the webhook serves, from server config or env vars."""
    return _config_value("META_PAGE_ID", PAGE_ID)


def webhook_app_secret():
    return _config_value("META_APP_SECRET", APP_SECRET)


def webhook_verify_token():
    return _config_value("META_VERIFY_TOKEN", VERIFY_TOKEN)


def verify_payload_signature(raw_body, signature):
    """Check Meta's X-Hub-Signature-256 header with the App Secret.

    Returns True when no app secret is configured (nothing to check) or when
    the signature matches; False otherwise.
    """
    secret = webhook_app_secret()
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _resolve(page_id=None, access_token=None, graph_version=None):
    """Resolve config from the caller (e.g. the session), falling back to env vars."""
    return (
        page_id or PAGE_ID,
        access_token or PAGE_ACCESS_TOKEN,
        graph_version or GRAPH_VERSION or DEFAULT_GRAPH_VERSION,
    )


def is_configured(page_id=None, access_token=None, graph_version=None):
    """True when Page credentials are available (explicit args or env vars)."""
    page_id, access_token, _ = _resolve(page_id, access_token, graph_version)
    return bool(page_id and access_token)


def send_message(recipient_id, text, page_id=None, access_token=None, graph_version=None):
    """Send message to user Page-ID via Meta Send API."""
    page_id, access_token, graph_version = _resolve(page_id, access_token, graph_version)
    if not page_id or not access_token:
        raise RuntimeError("Messenger is not configured.")

    url = (
        f"https://graph.facebook.com/"
        f"{graph_version}/{page_id}/messages"
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
        "Authorization": f"Bearer {access_token}",
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
    """GET Graph API endpoint and follow cursor pagination."""
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


def fetch_messages(page_id=None, access_token=None, graph_version=None):
    """Backfill inbound Messenger messages via Graph API.

    The webhook is the normal receive path (Meta pushes events to
    /messenger/webhook/). This is a fallback for catching up on messages
    delivered while the webhook was down or not yet subscribed: it lists the
    Page's conversations, reads each conversation's messages, and stores any
    inbound text messages not already in the database. Returns the number of
    new messages stored.

    Requires a Page Access Token with permission to read the Page's
    conversations (pages_messaging).
    """
    page_id, access_token, graph_version = _resolve(page_id, access_token, graph_version)
    if not page_id or not access_token:
        raise RuntimeError("Messenger is not configured.")

    headers = {"Authorization": f"Bearer {access_token}"}
    base = f"https://graph.facebook.com/{graph_version}"

    new = 0
    conversations = _get_paged(f"{base}/{page_id}/conversations", headers, {"fields": "id"})
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
            if not sender_id or str(sender_id) == str(page_id):
                continue  # our own outbound messages
            if Message.objects.filter(channel="messenger", user_email=str(page_id),
                                      message_id=msg["id"]).exists():
                continue  # already stored (e.g. webhook already received it)
            Message.objects.create(
                channel="messenger",
                contact=sender_id,
                direction="in",
                subject="",
                text=msg["message"],
                message_id=msg["id"],
                user_email=str(page_id),  # messages belong to the connected Page
            )
            new += 1
    return new
