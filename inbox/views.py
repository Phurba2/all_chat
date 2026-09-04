import json

from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from . import messenger
from .gmail import is_configured, fetch_emails, send_reply
from .models import Message


def _owner_filter(session_email):
    """Messages with no user_email are server-level (e.g. Messenger) and shared."""
    if session_email:
        return Q(user_email=session_email) | Q(user_email="")
    return Q(user_email="")


def logout(request):
    """Clear user session and logout"""
    # Clear session only - don't delete messages so user can see them again when they log back in
    request.session.flush()
    return redirect("setup")

def setup(request):
    """Handle Gmail credentials setup"""
    # If already logged in, redirect to inbox
    session_email = request.session.get("GMAIL_EMAIL")
    session_password = request.session.get("GMAIL_APP_PASSWORD")
    if is_configured(session_email, session_password):
        return redirect("inbox")
    
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
            
            return redirect("inbox")
        else:
            return render(request, "setup.html", {"error": "Please enter both email and app password."})
    
    return render(request, "setup.html")


def inbox(request, channel=None):
    # Check if Gmail is configured (env vars or session)
    session_email = request.session.get("GMAIL_EMAIL")
    session_password = request.session.get("GMAIL_APP_PASSWORD")
    gmail_configured = is_configured(session_email, session_password)
    messenger_configured = messenger.is_configured()
    
    # If clicking Gmail channel and not configured, redirect to setup
    if channel == "email" and not gmail_configured:
        return redirect("setup")
    
    # If no messages exist for this user and no credentials configured, redirect to setup
    user_messages_exist = Message.objects.filter(_owner_filter(session_email)).exists()
    if not user_messages_exist and not gmail_configured and not messenger_configured:
        return redirect("setup")
    
    names = dict(Message.CHANNELS)
    # Filter messages by current user
    qs = Message.objects.filter(_owner_filter(session_email))
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
        "messenger_configured": messenger_configured,
        "current_email": session_email if gmail_configured else None,
    })


def conversation(request, channel, contact):
    # Get current user's email
    session_email = request.session.get("GMAIL_EMAIL")
    
    # Filter thread by channel, contact, AND user
    thread = Message.objects.filter(channel=channel, contact=contact).filter(_owner_filter(session_email))
    if not thread.exists():
        raise Http404
    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if text:
            # Use session credentials if available
            session_password = request.session.get("GMAIL_APP_PASSWORD")
            
            if channel == "email" and is_configured(session_email, session_password):
                try:
                    send_reply(contact, text, in_reply_to=thread.last().message_id,
                              email=session_email, password=session_password)
                except Exception:
                    pass  # SMTP failed — the reply is still stored locally below
            elif channel == "messenger" and messenger.is_configured():
                try:
                    messenger.send_message(contact, text)
                except Exception:
                    pass  # Send API failed — the reply is still stored locally below
            # Messenger messages belong to the page (server-level), not to a Gmail session
            user_email = "" if channel == "messenger" else (session_email or "")
            Message.objects.create(channel=channel, contact=contact, direction="out", text=text, is_read=True, user_email=user_email)
            return redirect("conversation", channel=channel, contact=contact)
    thread.filter(direction="in", is_read=False).update(is_read=True)  # mark read on open
    
    return render(request, "conversation.html", {
        "thread": thread,
        "channel": channel,
        "channel_name": dict(Message.CHANNELS).get(channel, channel),
        "contact": contact,
        "current_email": session_email,
    })


@csrf_exempt
def messenger_webhook(request):
    """Endpoint Meta calls when Messenger events happen."""

    # Meta verifies webhook ownership with a GET before subscribing.
    if request.method == "GET":
        if (request.GET.get("hub.mode") == "subscribe"
                and request.GET.get("hub.verify_token") == messenger.VERIFY_TOKEN):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponse("Verification failed", status=403)

    # Actual Messenger events arrive as POSTs.
    if request.method == "POST":
        try:
            data = json.loads(request.body)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                message_data = event.get("message", {})
                if message_data.get("is_echo"):
                    continue  # our own outgoing message echoed back by Meta
                sender_id = event.get("sender", {}).get("id")
                text = message_data.get("text", "")
                if not sender_id or not text:
                    continue  # deliveries, read receipts, postbacks, attachments, ...
                message_id = message_data.get("mid", "")
                if message_id and Message.objects.filter(channel="messenger", message_id=message_id).exists():
                    continue  # Meta may redeliver; skip already-stored messages

                Message.objects.create(
                    channel="messenger",
                    contact=sender_id,
                    direction="in",
                    subject="",
                    text=text,
                    message_id=message_id,
                    user_email="",  # server-level; visible across sessions
                )

        return JsonResponse({"status": "ok"})

    return HttpResponse(status=405)
