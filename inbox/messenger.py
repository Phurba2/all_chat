import hashlib
import hmac
import json
import os
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
import requests
from .models import Message

def _env(key, default=""):
    """Read an env var at call time (not at import time).

    The module is often imported before Django's settings module calls
    load_dotenv(), so capturing os.environ.get() at module level would return
    None even when the .env file is present.  Always reading at call time means
    the value is correct regardless of import order.
    """
    return os.environ.get(key, default)


# Convenience shorthands used by legacy callers inside this module.
# They are intentionally evaluated lazily via _env() each time they're read.
def _PAGE_ID():        return _env("META_PAGE_ID")
def _PAGE_ACCESS_TOKEN(): return _env("META_PAGE_ACCESS_TOKEN")
def _APP_SECRET():     return _env("META_APP_SECRET")
def _VERIFY_TOKEN():   return _env("META_VERIFY_TOKEN")
def _GRAPH_VERSION():  return _env("META_GRAPH_VERSION")


# Keep module-level names for any external code that reads them directly,
# but they are now populated lazily when first accessed via _resolve()
# or the webhook helper functions below.
PAGE_ID = _env("META_PAGE_ID")           # legacy compatibility; prefer _resolve()
PAGE_ACCESS_TOKEN = _env("META_PAGE_ACCESS_TOKEN")
APP_SECRET = _env("META_APP_SECRET")
VERIFY_TOKEN = _env("META_VERIFY_TOKEN")
GRAPH_VERSION = _env("META_GRAPH_VERSION")

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
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = CONFIG_PATH.with_suffix(".tmp")
    with open(temporary_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    temporary_path.replace(CONFIG_PATH)


def save_server_config(page_id=None, access_token=None, app_secret=None,
                       verify_token=None, graph_version=None):
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
    """Read persisted config, preserving legacy module-level overrides."""
    return _read_server_config().get(key) or env_fallback

def webhook_page_id():
    return _config_value("META_PAGE_ID", PAGE_ID)

def webhook_app_secret():
    return _config_value("META_APP_SECRET", APP_SECRET)

def webhook_verify_token():
    return _config_value("META_VERIFY_TOKEN", VERIFY_TOKEN)

def verify_payload_signature(raw_body, signature):
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
    """Resolve config from the caller, server config file, then env vars.

    The module-level vars (PAGE_ID, PAGE_ACCESS_TOKEN, …) are set to None
    because os.environ.get() at import time races with load_dotenv() in
    settings.py.  Reading the server config JSON and calling _env() at call
    time is resilient to that race and also picks up tokens saved via the
    setup UI.
    """
    cfg = _read_server_config()
    return (
        page_id or cfg.get("META_PAGE_ID") or PAGE_ID,
        access_token or cfg.get("META_PAGE_ACCESS_TOKEN") or PAGE_ACCESS_TOKEN,
        graph_version or cfg.get("META_GRAPH_VERSION") or GRAPH_VERSION or DEFAULT_GRAPH_VERSION,
    )


def is_configured(page_id=None, access_token=None, graph_version=None):
    page_id, access_token, _ = _resolve(page_id, access_token, graph_version)
    return bool(page_id and access_token)


def resolve_psid(contact, page_id=None, access_token=None, graph_version=None):
    """Resolve a contact name or id to a numeric Page-Scoped ID (PSID)."""
    if not contact:
        return ""
    contact_str = str(contact).strip()
    if contact_str.isdigit():
        return contact_str

    # 1. Look in existing messages in the DB
    match = Message.objects.filter(channel="messenger", contact=contact_str).exclude(contact_id="").values_list("contact_id", flat=True).first()
    if match:
        return match

    # 2. Query Page conversations via Graph API to find participant by name
    page_id, access_token, graph_version = _resolve(page_id, access_token, graph_version)
    if not page_id or not access_token:
        return ""

    try:
        url = f"https://graph.facebook.com/{graph_version}/{page_id}/conversations"
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(url, params={"fields": "id,participants"}, headers=headers, timeout=10)
        if resp.ok:
            for conv in resp.json().get("data", []):
                for p in (conv.get("participants") or {}).get("data", []):
                    if p.get("name", "").strip().lower() == contact_str.lower() and str(p.get("id")) != str(page_id):
                        psid = str(p["id"])
                        Message.objects.filter(channel="messenger", contact=contact_str, contact_id="").update(contact_id=psid)
                        return psid
    except Exception:
        pass

    return ""


def send_message(recipient_id, text, page_id=None, access_token=None, graph_version=None):
    page_id, access_token, graph_version = _resolve(page_id, access_token, graph_version)
    if not page_id or not access_token:
        raise RuntimeError("Messenger is not configured.")

    recipient_str = str(recipient_id).strip()
    resolved = resolve_psid(recipient_str, page_id=page_id, access_token=access_token, graph_version=graph_version)
    if resolved:
        recipient_str = resolved

    url = (
        f"https://graph.facebook.com/"
        f"{graph_version}/{page_id}/messages"
    )

    payload = {
        "recipient": {
            "id": recipient_str
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

    if not response.ok:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Meta Send API error ({response.status_code}): {detail}")

    return response.json()


def _get_paged(url, headers, params=None, max_pages=10):
    items = []
    for _ in range(max_pages):
        response = requests.get(url, params=params, headers=headers, timeout=15)
        if not response.ok:
            # Meta's error body (code/message) is far more useful than a bare
            # HTTPError, especially for 401s (bad/expired token) vs 400s
            # (blocked field/permission) — surface it instead of swallowing it.
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RuntimeError(
                f"Graph API request failed ({response.status_code}) for {url}: {detail}"
            )
        payload = response.json()
        items.extend(payload.get("data", []))
        next_url = (payload.get("paging") or {}).get("next")
        if not next_url:
            break
        url = next_url
        params = None  # paging.next already carries the cursor
    return items


def _fetch_user_name(psid, headers, base, cache):
    """Look up a sender's display name via Graph API, with per-run caching.

    Falls back to the raw PSID if the lookup fails or the app doesn't have
    permission to read profile fields for this user (Meta restricts this
    endpoint; many apps need advanced access approval to get real names back).
    """
    if not psid:
        return ""
    if psid in cache:
        return cache[psid]
    try:
        response = requests.get(
            f"{base}/{psid}",
            params={"fields": "name"},
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        name = (data.get("name") or "").strip()
    except (requests.RequestException, ValueError):
        name = ""
    cache[psid] = name or psid
    return cache[psid]


def fetch_messages(page_id=None, access_token=None, graph_version=None, include_outbound=False):
    """Backfill Messenger messages via Graph API.

    Fetches conversations and messages (both incoming from users and outgoing
    replies from the Page), ensuring the full two-way chat history is stored
    chronologically in the database with recipient PSID and sender names.
    Returns the number of new messages stored.
    """
    page_id, access_token, graph_version = _resolve(page_id, access_token, graph_version)
    if not page_id or not access_token:
        raise RuntimeError("Messenger is not configured.")

    headers = {"Authorization": f"Bearer {access_token}"}
    base = f"https://graph.facebook.com/{graph_version}"
    name_cache = {}

    new = 0
    conversations = _get_paged(
        f"{base}/{page_id}/conversations",
        headers,
        {"fields": "id,participants"},
    )
    for conv in conversations:
        conv_id = conv.get("id")
        if not conv_id:
            continue

        # Conversation-level participants carry PSID and name
        other_psid = None
        other_name = None
        for participant in (conv.get("participants") or {}).get("data", []):
            p_id = str(participant.get("id") or "")
            p_name = (participant.get("name") or "").strip()
            if p_id and p_name and p_id not in name_cache:
                name_cache[p_id] = p_name
            if p_id and p_id != str(page_id):
                other_psid = p_id
                other_name = p_name

        messages = _get_paged(
            f"{base}/{conv_id}/messages",
            headers,
            {"fields": "id,message,from,to,created_time"},
        )
        for msg in messages:
            if not msg.get("id") or not msg.get("message"):
                continue  # no text (attachments, reactions, ...)

            sender_id = str((msg.get("from") or {}).get("id") or "")
            if not sender_id:
                continue

            raw_ts = msg.get("created_time", "")
            try:
                sent_at = datetime.fromisoformat(raw_ts.replace("+0000", "+00:00"))
            except (ValueError, AttributeError):
                sent_at = datetime.now(dt_timezone.utc)

            is_outbound = (sender_id == str(page_id))
            if is_outbound:
                if not include_outbound:
                    continue
                to_list = (msg.get("to") or {}).get("data", [])
                recipient_id = str(to_list[0].get("id")) if to_list and to_list[0].get("id") else other_psid
                target_psid = recipient_id or ""
                contact_name = _fetch_user_name(target_psid, headers, base, name_cache) or other_name or target_psid
                direction = "out"
            else:
                target_psid = sender_id
                contact_name = _fetch_user_name(target_psid, headers, base, name_cache) or other_name or target_psid
                direction = "in"

            if Message.objects.filter(channel="messenger", user_email=str(page_id),
                                      message_id=msg["id"]).exists():
                if target_psid:
                    Message.objects.filter(channel="messenger", user_email=str(page_id),
                                           message_id=msg["id"], contact_id="").update(contact_id=target_psid)
                continue

            Message.objects.create(
                channel="messenger",
                contact=contact_name,
                contact_id=target_psid,
                direction=direction,
                subject="",
                text=msg["message"],
                message_id=msg["id"],
                user_email=str(page_id),
                created_at=sent_at,
                is_read=True if is_outbound else False,
            )
            new += 1

        # Backfill any older messages in this conversation that lack contact_id
        if other_psid and other_name:
            Message.objects.filter(channel="messenger", contact=other_name, contact_id="").update(contact_id=other_psid)

    return new
