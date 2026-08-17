from django.http import Http404
from django.shortcuts import redirect, render

from .gmail import is_configured, send_reply
from .models import Message


def inbox(request, channel=None):
    """Group all messages by (channel, contact) into conversations, optionally filtered by channel."""
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
    return render(request, "inbox.html", {"conversations": order, "current": channel, "channels": Message.CHANNELS})


def conversation(request, channel, contact):
    thread = Message.objects.filter(channel=channel, contact=contact)
    if not thread.exists():
        raise Http404
    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if text:
            if channel == "email" and is_configured():
                try:
                    send_reply(contact, text, in_reply_to=thread.last().message_id)
                except Exception:
                    pass  # SMTP failed — the reply is still stored locally below
            Message.objects.create(channel=channel, contact=contact, direction="out", text=text, is_read=True)
            return redirect("conversation", channel=channel, contact=contact)
    thread.filter(direction="in", is_read=False).update(is_read=True)  # mark read on open
    return render(request, "conversation.html", {"thread": thread, "channel": channel, "channel_name": dict(Message.CHANNELS).get(channel, channel), "contact": contact})
