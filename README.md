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

Gmail can be connected per-user from the browser at `/setup/` (email + app password), or configured server-side via environment variables:

```bash
export GMAIL_EMAIL=you@gmail.com
export GMAIL_APP_PASSWORD=your-app-password
.venv/bin/python manage.py fetch_gmail
```

## Messenger Setup

Messenger can be connected from the browser at `/setup/messenger/` (Page ID + Page Access Token), just like Gmail. Credentials are stored in the browser session, and on connect the app backfills recent conversations via the Graph API.

The same page also collects the **App Secret** and **Verify Token**. These are saved server-side (to a gitignored `messenger_server_config.json`) because Meta calls the webhook without a browser session — session storage wouldn't work for them. They can alternatively be set via environment variables:

```bash
export META_APP_SECRET=your-app-secret
export META_VERIFY_TOKEN=something-you-create
export META_GRAPH_VERSION=v21.0
```

For real-time message delivery, the webhook must be configured server-side. Server config saved from the setup page takes precedence over these environment variables (add them to your `.env` if you prefer):

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

> Messenger works like Gmail: each browser connects the Page it wants to use, and
> messages are stored under that Page, so each connected Page only sees its own
> conversations. Env vars configure the server-side webhook only; they do not
> unlock the Messenger channel in the browser.

## Project Structure

- `inbox/models.py` — Message model
- `inbox/views.py` — Conversation list & thread views
- `templates/` — HTML templates

## License

MIT
