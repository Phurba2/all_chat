# Unified Inbox

A minimal Django app that pulls messages from WhatsApp, Messenger, Gmail, and TikTok into a single dashboard. Messages from all four channels live in one table and are grouped into conversations, with tabs to filter by platform. Gmail is fully wired (IMAP fetch + SMTP reply); the others are stubs awaiting their integrations.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000 — you'll see the unified inbox with platform tabs (All / WhatsApp / Messenger / Gmail / TikTok). Click any conversation to view the thread and reply. Email replies are sent for real; other channels store replies locally until their integrations are added.

## Structure

| File | Purpose |
|---|---|
| `inbox/models.py` | Single `Message` model (channel, contact, direction, text, read status) |
| `inbox/views.py` | `inbox` (conversation list with channel filter) and `conversation` (thread + reply) |
| `inbox/management/commands/seed.py` | Populates sample messages for every channel |
| `templates/` | `base.html`, `inbox.html`, `conversation.html` (minimalist, inline CSS) |
| `admin/` | Manage messages via Django admin |

## Gmail integration

Emails are pulled in over IMAP and replies are sent over SMTP — no extra dependencies, just environment variables:

```bash
export GMAIL_EMAIL=you@gmail.com
export GMAIL_APP_PASSWORD=your-app-password   # https://myaccount.google.com/apppasswords
.venv/bin/python manage.py fetch_gmail        # idempotent: already-seen emails are skipped
```

Replies typed in an email thread are sent for real via SMTP (threaded with `In-Reply-To`). If credentials aren't configured or SMTP fails, the reply is still stored locally so nothing is lost. Run `fetch_gmail` on a schedule (cron / Celery beat) later to keep the inbox live.

## Adding a new channel later

Add the channel name to `Message.CHANNELS` in `inbox/models.py`, then write a small receiver (e.g. a webhook view or message parser) that creates `Message` objects — the inbox and its tabs pick them up automatically.
