# Unified Inbox

A minimal Django app that pulls messages from WhatsApp, Instagram, Messenger, Email, and website chat into a single dashboard. Currently it's a simple working model — messages from all channels live in one table and are grouped into conversations. Real channel integrations (webhooks/APIs) come later.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed          # optional sample conversations
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000 — you'll see the unified inbox. Click any conversation to view the thread and reply (replies are stored as outgoing messages; no real sending yet).

## Structure

| File | Purpose |
|---|---|
| `inbox/models.py` | Single `Message` model (channel, contact, direction, text, read status) |
| `inbox/views.py` | `inbox` (conversation list) and `conversation` (thread + reply) |
| `inbox/management/commands/seed.py` | Populates sample messages for every channel |
| `templates/` | `base.html`, `inbox.html`, `conversation.html` (minimalist, inline CSS) |
| `admin/` | Manage messages via Django admin |

## Adding a new channel later

Add the channel name to `Message.CHANNELS` in `inbox/models.py`, then write a small receiver (e.g. a webhook view or email parser) that creates `Message` objects — the inbox picks them up automatically.
