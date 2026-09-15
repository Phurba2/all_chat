import hashlib
import hmac
import json
import os
import requests
from pathlib import Path


def _env(key, default=""):
    return os.environ.get(key, default)


PHONE_NUMBER_ID = _env("WHATSAPP_PHONE_NUMBER_ID") or None
ACCESS_TOKEN = _env("WHATSAPP_ACCESS_TOKEN") or None
APP_SECRET = _env("WHATSAPP_APP_SECRET") or None
VERIFY_TOKEN = _env("WHATSAPP_VERIFY_TOKEN") or None
GRAPH_VERSION = _env("WHATSAPP_GRAPH_VERSION") or None

DEFAULT_GRAPH_VERSION = "v26.0"

CONFIG_PATH = Path(__file__).resolve().parent.parent / "whatsapp_server_config.json"


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


def save_server_config(phone_number_id=None, access_token=None, app_secret=None,
                       verify_token=None, graph_version=None):
    data = _read_server_config()
    values = {
        "WHATSAPP_PHONE_NUMBER_ID": phone_number_id,
        "WHATSAPP_ACCESS_TOKEN": access_token,
        "WHATSAPP_APP_SECRET": app_secret,
        "WHATSAPP_VERIFY_TOKEN": verify_token,
        "WHATSAPP_GRAPH_VERSION": graph_version,
    }
    for key, value in values.items():
        if value:
            data[key] = value
    _write_server_config(data)


def _config_value(key, env_fallback):
    return _read_server_config().get(key) or env_fallback


def webhook_app_secret():
    return _config_value("WHATSAPP_APP_SECRET", APP_SECRET)


def webhook_verify_token():
    return _config_value("WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)


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


def _resolve():
    cfg = _read_server_config()
    return (
        cfg.get("WHATSAPP_PHONE_NUMBER_ID") or PHONE_NUMBER_ID,
        cfg.get("WHATSAPP_ACCESS_TOKEN") or ACCESS_TOKEN,
        cfg.get("WHATSAPP_GRAPH_VERSION") or GRAPH_VERSION or DEFAULT_GRAPH_VERSION,
    )


def is_configured():
    phone_number_id, access_token, _ = _resolve()
    return bool(phone_number_id and access_token)


def send_message(recipient_number, text):
    phone_number_id, access_token, graph_version = _resolve()
    if not phone_number_id or not access_token:
        raise RuntimeError("WhatsApp is not configured.")

    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(recipient_number).strip(),
        "type": "text",
        "text": {"body": text},
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, json=payload, headers=headers, timeout=15)

    if not response.ok:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(f"WhatsApp Cloud API error ({response.status_code}): {detail}")

    return response.json()


