import json

from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from . import messenger
from .gmail import is_configured, fetch_emails, send_reply
from .models import Message


def _messenger_session_config(request):
    """Read Messenger credentials stored in the browser session (like Gmail)."""
    return (
        request.session.get("META_PAGE_ID"),
        request.session.get("META_PAGE_ACCESS_TOKEN"),
        request.session.get("META_GRAPH_VERSION"),
    )


def _messenger_session_connected(request):
    """Whether the browser session has connected a Messenger Page (like Gmail).

    Server-level env config (META_PAGE_ID & co) does NOT count: Messenger is
    per-session/per-page, so each browser must connect the Page it wants.
    """
    page_id, access_token, _ = _messenger_session_config(request)
    return bool(page_id and access_token)


def _owner_filter(channel, session_email=None, messenger_page_id=None):
    """Messages visible to the current session, scoped per account like Gmail.

    Email messages belong to the connected Gmail address. Messenger messages
    belong to the connected Facebook Page (stored under the Page id). Server
    -level Gmail messages (user_email="") stay shared for legacy setups.
    """
    if channel == "messenger":
        if messenger_page_id:
            return Q(user_email=str(messenger_page_id))
        return Q(pk__in=[])  # nothing visible without a connected Page
    if session_email:
        return Q(user_email=session_email) | Q(user_email="")
    return Q(user_email="")


def _session_user_label(request):
    """Account chip shown in the header (with Logout) for per-user sessions.

    Gmail sessions are identified by the connected email; Messenger sessions
    by the connected Page. Returns None when the session only relies on
    server-level env config, in which case there is nothing to log out of.
    """
    email = request.session.get("GMAIL_EMAIL")
    if email:
        return f"👤 {email}"
    if _messenger_session_connected(request):
        page_id, _, _ = _messenger_session_config(request)
        return f"💬 Page {page_id}"
    return None


def logout(request):
    """Clear user session and logout"""
    # Remember which accounts were logged in so we land on the right setup page
    had_gmail = bool(request.session.get("GMAIL_EMAIL"))
    had_messenger = _messenger_session_connected(request)
    # Clear session only - don't delete messages so user can see them again when they log back in
    request.session.flush()
    if had_messenger and not had_gmail:
        return redirect("setup_messenger")
    return redirect("setup")

def setup(request):
    """Handle Gmail credentials setup"""
    # If already logged in, go to the Gmail inbox
    session_email = request.session.get("GMAIL_EMAIL")
    session_password = request.session.get("GMAIL_APP_PASSWORD")
    if is_configured(session_email, session_password):
        return redirect("channel", channel="email")
    
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        
        if email and password:
            # Store credentials in session
            request.session["GMAIL_EMAIL"] = email
            request.session["GMAIL_APP_PASSWORD"] = password
            
            try:
                # Try to fetch emails with the provided credentials
                new_count = fetch_emails(email=email, password=password, user_email=email)
                request.session["setup_message"] = f"Successfully connected! Fetched {new_count} new email(s)."
            except Exception as e:
                request.session["setup_message"] = f"Connected, but couldn't fetch emails: {str(e)}"
            
            return redirect("channel", channel="email")
        else:
            return render(request, "setup.html", {"error": "Please enter both email and app password."})
    
    return render(request, "setup.html")


def setup_messenger(request):
    """Handle Messenger credentials setup (session-based, like Gmail)."""
    page_id, access_token, graph_version = _messenger_session_config(request)
    # If already configured for this session, go straight to the Messenger inbox
    if _messenger_session_connected(request):
        return redirect("channel", channel="messenger")

    if request.method == "POST":
        page_id = request.POST.get("page_id", "").strip()
        access_token = request.POST.get("access_token", "").strip()
        graph_version = request.POST.get("graph_version", "").strip()
        app_secret = request.POST.get("app_secret", "").strip()
        verify_token = request.POST.get("verify_token", "").strip()

        # Webhook credentials (App Secret, Verify Token) are saved server-side:
        # Meta calls /messenger/webhook/ without a browser session, so session
        # storage wouldn't work for them. Save them even when the Page fields
        # are incomplete so webhook setup doesn't require re-entering a token.
        webhook_saved = False
        if app_secret or verify_token:
            messenger.save_server_config(app_secret=app_secret, verify_token=verify_token)
            webhook_saved = True

        if page_id and access_token:
            # Store credentials in session
            request.session["META_PAGE_ID"] = page_id
            request.session["META_PAGE_ACCESS_TOKEN"] = access_token
            if graph_version:
                request.session["META_GRAPH_VERSION"] = graph_version

            try:
                # Try to backfill messages with the provided credentials
                new_count = messenger.fetch_messages(
                    page_id=page_id,
                    access_token=access_token,
                    graph_version=graph_version or messenger.DEFAULT_GRAPH_VERSION,
                )
                message = f"Successfully connected! Fetched {new_count} new message(s)."
                if webhook_saved:
                    message += " Webhook credentials saved."
                request.session["setup_message"] = message
            except Exception as e:
                request.session["setup_message"] = f"Connected, but couldn't fetch messages: {str(e)}"

            return redirect("channel", channel="messenger")
        else:
            return render(request, "messenger_setup.html", {
                "error": "Please enter both Page ID and Page Access Token.",
                "page_id": page_id,
                "access_token": access_token,
                "graph_version": graph_version,
                "app_secret": app_secret,
                "verify_token": verify_token,
            })

    return render(request, "messenger_setup.html", {
        "graph_version": graph_version or messenger.GRAPH_VERSION or messenger.DEFAULT_GRAPH_VERSION,
    })


def inbox(request, channel=None):
    # Check if Gmail is configured (env vars or session)
    session_email = request.session.get("GMAIL_EMAIL")
    session_password = request.session.get("GMAIL_APP_PASSWORD")
    gmail_configured = is_configured(session_email, session_password)

    # Messenger is per-session/per-page (like Gmail) — env vars do NOT count
    messenger_connected = _messenger_session_connected(request)
    messenger_page_id = request.session.get("META_PAGE_ID") if messenger_connected else None
    
    # If clicking Gmail channel and not configured, redirect to setup
    if channel == "email" and not gmail_configured:
        return redirect("setup")

    # If clicking Messenger channel and not connected, redirect to its setup
    if channel == "messenger" and not messenger_connected:
        return redirect("setup_messenger")

    # If no messages exist for this user and nothing is configured, redirect to setup
    owner = _owner_filter(channel or "", session_email, messenger_page_id)
    user_messages_exist = Message.objects.filter(owner).exists()
    if not user_messages_exist and not gmail_configured and not messenger_connected:
        return redirect("setup")
    
    names = dict(Message.CHANNELS)
    # Filter messages by current user
    qs = Message.objects.filter(_owner_filter(channel or "", session_email, messenger_page_id))
    if channel:
        qs = qs.filter(channel=channel)
    conversations = {}
    for m in qs:
        key = (m.channel, m.contact)
        conv = conversations.setdefault(key, {"channel": m.channel, "channel_name": names.get(m.channel, m.channel), "contact": m.contact, "unread": 0, "last": m})
        if not m.is_read and m.direction == "in":
            conv["unread"] += 1
        if m.created_at >= conv["last"].created_at:
            conv["last"] = m
    order = sorted(conversations.values(), key=lambda c: c["last"].created_at, reverse=True)
    
    # Get setup message from session
    setup_message = request.session.pop("setup_message", None)
    
    return render(request, "inbox.html", {
        "conversations": order,
        "current": channel,
        "channels": Message.CHANNELS,
        "setup_message": setup_message,
        "gmail_configured": gmail_configured,
        "messenger_configured": messenger_connected,
        "current_email": _session_user_label(request),
    })


def conversation(request, channel, contact):
    # Get current user's email
    session_email = request.session.get("GMAIL_EMAIL")
    messenger_connected = _messenger_session_connected(request)
    messenger_page_id = request.session.get("META_PAGE_ID") if messenger_connected else None

    # Filter thread by channel, contact, AND owner
    owner = _owner_filter(channel, session_email, messenger_page_id)
    thread = Message.objects.filter(channel=channel, contact=contact).filter(owner)
    if not thread.exists():
        raise Http404
    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if text:
            # Use session credentials if available
            session_password = request.session.get("GMAIL_APP_PASSWORD")
            page_id, access_token, graph_version = _messenger_session_config(request)
            
            if channel == "email" and is_configured(session_email, session_password):
                try:
                    send_reply(contact, text, in_reply_to=thread.last().message_id,
                              email=session_email, password=session_password)
                except Exception:
                    pass  # SMTP failed — the reply is still stored locally below
            elif channel == "messenger" and messenger_connected:
                try:
                    messenger.send_message(contact, text,
                                           page_id=page_id, access_token=access_token,
                                           graph_version=graph_version)
                except Exception:
                    pass  # Send API failed — the reply is still stored locally below
            # Messenger messages belong to the connected Page (like Gmail's email)
            user_email = str(messenger_page_id) if channel == "messenger" else (session_email or "")
            Message.objects.create(channel=channel, contact=contact, direction="out", text=text, is_read=True, user_email=user_email)
            return redirect("conversation", channel=channel, contact=contact)
    thread.filter(direction="in", is_read=False).update(is_read=True)  # mark read on open
    
    return render(request, "conversation.html", {
        "thread": thread,
        "channel": channel,
        "channel_name": dict(Message.CHANNELS).get(channel, channel),
        "contact": contact,
        "current_email": _session_user_label(request),
    })


@csrf_exempt
def messenger_webhook(request):
    """Endpoint Meta calls when Messenger events happen."""

    # Meta verifies webhook ownership with a GET before subscribing.
    if request.method == "GET":
        if (request.GET.get("hub.mode") == "subscribe"
                and request.GET.get("hub.verify_token") == messenger.webhook_verify_token()):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponse("Verification failed", status=403)

    # Actual Messenger events arrive as POSTs.
    if request.method == "POST":
        # Meta signs every event with the App Secret (X-Hub-Signature-256).
        if not messenger.verify_payload_signature(request.body,
                                                  request.headers.get("X-Hub-Signature-256")):
            return HttpResponse("Signature verification failed", status=403)
        try:
            data = json.loads(request.body)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        for entry in data.get("entry", []):
            # Each entry is a Page that received messages; the payload carries its id.
            page_id = str(entry.get("id") or messenger.webhook_page_id() or "")
            if not page_id:
                continue
            for event in entry.get("messaging", []):
                message_data = event.get("message", {})
                if message_data.get("is_echo"):
                    continue  # our own outgoing message echoed back by Meta
                sender_id = event.get("sender", {}).get("id")
                text = message_data.get("text", "")
                if not sender_id or not text:
                    continue  # deliveries, read receipts, postbacks, attachments, ...
                message_id = message_data.get("mid", "")
                if message_id and Message.objects.filter(channel="messenger", user_email=page_id,
                                                         message_id=message_id).exists():
                    continue  # Meta may redeliver; skip already-stored messages

                Message.objects.create(
                    channel="messenger",
                    contact=sender_id,
                    direction="in",
                    subject="",
                    text=text,
                    message_id=message_id,
                    user_email=page_id,  # belongs to the Page that received it
                )

        return JsonResponse({"status": "ok"})

    return HttpResponse(status=405)
