from django.http import Http404
from django.shortcuts import redirect, render
from .gmail import is_configured, fetch_emails, send_reply
from .models import Message

def setup(request):
    """Handle Gmail credentials setup"""
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        
        if email and password:
            # Store credentials in session
            request.session["GMAIL_EMAIL"] = email
            request.session["GMAIL_APP_PASSWORD"] = password
            
            try:
                # Try to fetch emails with the provided credentials
                new_count = fetch_emails(email=email, password=password)
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
    
    # If no messages exist and no credentials configured, redirect to setup
    if not Message.objects.exists() and not is_configured(session_email, session_password):
        return redirect("setup")
    
    names = dict(Message.CHANNELS)
    qs = Message.objects.all()
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
    })


def conversation(request, channel, contact):
    thread = Message.objects.filter(channel=channel, contact=contact)
    if not thread.exists():
        raise Http404
    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if text:
            # Use session credentials if available
            session_email = request.session.get("GMAIL_EMAIL")
            session_password = request.session.get("GMAIL_APP_PASSWORD")
            
            if channel == "email" and is_configured(session_email, session_password):
                try:
                    send_reply(contact, text, in_reply_to=thread.last().message_id,
                              email=session_email, password=session_password)
                except Exception:
                    pass  # SMTP failed — the reply is still stored locally below
            Message.objects.create(channel=channel, contact=contact, direction="out", text=text, is_read=True)
            return redirect("conversation", channel=channel, contact=contact)
    thread.filter(direction="in", is_read=False).update(is_read=True)  # mark read on open
    return render(request, "conversation.html", {"thread": thread, "channel": channel, "channel_name": dict(Message.CHANNELS).get(channel, channel), "contact": contact})
