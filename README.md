# Unified Inbox

A minimal Django app that pulls messages from WhatsApp, Messenger, and Gmail into a single dashboard.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000

## Gmail Setup

```bash
export GMAIL_EMAIL=you@gmail.com
export GMAIL_APP_PASSWORD=your-app-password
.venv/bin/python manage.py fetch_gmail
```

## Messenger Setup

Messenger is configured server-side via environment variables (add them to your `.env`):

```bash
export META_PAGE_ID=your-page-id
export META_PAGE_ACCESS_TOKEN=your-page-access-token
export META_APP_SECRET=your-app-secret
export META_VERIFY_TOKEN=something-you-create
export META_GRAPH_VERSION=v21.0
```

Meta sends incoming messages to the webhook URL (needs to be publicly reachable over HTTPS):

```text
https://your-domain.com/messenger/webhook/
```

Register that URL with the `verify_token` above in your Meta app's Messenger webhook settings. Outgoing replies are sent with the Page Access Token via the Graph API Send API.

To catch up on messages delivered while the webhook was down or not yet subscribed, backfill from the Graph API (the token needs `pages_messaging` permission to read conversations):

```bash
.venv/bin/python manage.py fetch_messenger
```

Running it repeatedly is safe — messages already in the database are skipped.

> Messenger messages are stored without a `user_email` because they belong to the Page, not to an individual Gmail session, so they show up for everyone using the inbox.


## Project Structure

- `inbox/models.py` — Message model
- `inbox/views.py` — Conversation list & thread views
- `templates/` — HTML templates

## License

MIT
