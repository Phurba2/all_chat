import json
from datetime import datetime, timezone as dt_timezone

from django.db import IntegrityError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import gmail, messenger, whatsapp
from .models import Message


def inbox(request, channel=None):
    channels = dict(Message.CHANNELS)
    qs = Message.objects.all()
    if channel:
        qs = qs.filter(channel=channel)
    conversations = {}
    for m in qs:
        key = (m.channel, m.contact)
        conv = conversations.setdefault(key, {"channel": m.channel, "channel_name": channels.get(m.channel, m.channel), "contact": m.contact, "unread": 0, "last": m})
        if not m.is_read and m.direction == "in":
            conv["unread"] += 1
        if m.created_at >= conv["last"].created_at:
            conv["last"] = m
    order = sorted(conversations.values(), key=lambda c: c["last"].created_at, reverse=True)
    return render(request, "inbox.html", {
        "conversations": order,
        "current": channel,
        "channels": Message.CHANNELS,
    })


def conversation(request, channel, contact):
    thread = Message.objects.filter(channel=channel, contact=contact).order_by("created_at")
    if not thread.exists():
        raise Http404

    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if text:
            sent_mid = ""
            if channel == "email":
                last_in = thread.filter(direction="in").last()
                try:
                    gmail.send_reply(contact, text, in_reply_to=last_in.message_id if last_in else None)
                except Exception as e:
                    sent_mid = ""
            elif channel == "messenger":
                psid = thread.exclude(contact_id="").values_list("contact_id", flat=True).last() or contact
                try:
                    res = messenger.send_message(psid, text)
                    if isinstance(res, dict):
                        sent_mid = res.get("message_id", "")
                except Exception:
                    sent_mid = ""
            elif channel == "whatsapp":
                wa_id = thread.exclude(contact_id="").values_list("contact_id", flat=True).last() or contact
                try:
                    res = whatsapp.send_message(wa_id, text)
                    if isinstance(res, dict):
                        sent_mid = ((res.get("messages") or [{}])[0].get("id") or "")
                except Exception:
                    sent_mid = ""
            contact_id_out = ""
            if channel == "messenger":
                contact_id_out = psid
            elif channel == "whatsapp":
                contact_id_out = wa_id
            Message.objects.create(
                channel=channel,
                contact=contact,
                contact_id=contact_id_out,
                direction="out",
                text=text,
                message_id=sent_mid,
                is_read=True,
            )
            return redirect("conversation", channel=channel, contact=contact)
    thread.filter(direction="in", is_read=False).update(is_read=True)

    return render(request, "conversation.html", {
        "thread": thread,
        "channel": channel,
        "channel_name": dict(Message.CHANNELS).get(channel, channel),
        "contact": contact,
    })


@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "GET":
        if (request.GET.get("hub.mode") == "subscribe"
                and request.GET.get("hub.verify_token") == whatsapp.webhook_verify_token()):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponse("Verification failed", status=403)

    if request.method == "POST":
        if not whatsapp.verify_payload_signature(request.body,
                                                 request.headers.get("X-Hub-Signature-256")):
            return HttpResponse("Signature verification failed", status=403)
        try:
            data = json.loads(request.body)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                phone_number_id = str(metadata.get("phone_number_id") or "")

                profiles = {}
                for contact in value.get("contacts", []):
                    wa_id = str(contact.get("wa_id") or "")
                    name = ((contact.get("profile") or {}).get("name") or "").strip()
                    if wa_id:
                        profiles[wa_id] = name

                for msg in value.get("messages", []):
                    sender_wa_id = str(msg.get("from") or "")
                    text = (msg.get("text") or {}).get("body", "")
                    if not sender_wa_id or not text:
                        continue
                    message_id = msg.get("id", "")
                    if message_id and Message.objects.filter(channel="whatsapp", user_email=phone_number_id,
                                                             message_id=message_id).exists():
                        continue
                    raw_ts = msg.get("timestamp")
                    try:
                        sent_at = datetime.fromtimestamp(int(raw_ts), tz=dt_timezone.utc)
                    except (TypeError, ValueError, OSError):
                        sent_at = timezone.now()
                    contact_name = profiles.get(sender_wa_id) or sender_wa_id
                    try:
                        Message.objects.create(
                            channel="whatsapp",
                            contact=contact_name,
                            contact_id=sender_wa_id,
                            direction="in",
                            subject="",
                            text=text,
                            message_id=message_id,
                            user_email=phone_number_id,
                            created_at=sent_at,
                            is_read=False,
                        )
                    except IntegrityError:
                        continue
                    reply_text = _build_whatsapp_reply(text)
                    if reply_text:
                        try:
                            res = whatsapp.send_message(sender_wa_id, reply_text)
                            reply_mid = ((res.get("messages") or [{}])[0].get("id") or "") if isinstance(res, dict) else ""
                            Message.objects.create(
                                channel="whatsapp",
                                contact=contact_name,
                                contact_id=sender_wa_id,
                                direction="out",
                                subject="",
                                text=reply_text,
                                message_id=reply_mid,
                                user_email=phone_number_id,
                                created_at=timezone.now(),
                                is_read=True,
                            )
                        except Exception as e:
                            print(f"[whatsapp] Auto-reply failed: {e}")

        return JsonResponse({"status": "ok"})

    return HttpResponse(status=405)


def _build_whatsapp_reply(incoming_text):
    text = (incoming_text or "").strip().lower()
    if text in ("hi", "hello", "hey", "start"):
        return "Hello! 👋 Thanks for messaging us. How can I help you today?"
    if text == "help":
        return "Available commands:\n• hi — greet\n• help — this menu\n• bye — goodbye"
    if text in ("bye", "goodbye"):
        return "Goodbye! 👋 Have a great day."
    if text == "ping":
        return "pong 🏓"
    return f"Thanks for your message: \"{incoming_text.strip()}\". A human will reply shortly."


@csrf_exempt
def messenger_webhook(request):
    if request.method == "GET":
        if (request.GET.get("hub.mode") == "subscribe"
                and request.GET.get("hub.verify_token") == messenger.webhook_verify_token()):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponse("Verification failed", status=403)

    if request.method == "POST":
        if not messenger.verify_payload_signature(request.body,
                                                  request.headers.get("X-Hub-Signature-256")):
            return HttpResponse("Signature verification failed", status=403)
        try:
            data = json.loads(request.body)
        except ValueError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        for entry in data.get("entry", []):
            page_id = str(entry.get("id") or messenger.webhook_page_id() or "")
            if not page_id:
                continue
            for event in entry.get("messaging", []):
                message_data = event.get("message", {})
                if message_data.get("is_echo"):
                    continue
                sender_id = str(event.get("sender", {}).get("id") or "")
                text = message_data.get("text", "")
                if not sender_id or not text:
                    continue
                message_id = message_data.get("mid", "")
                if message_id and Message.objects.filter(channel="messenger", user_email=page_id,
                                                         message_id=message_id).exists():
                    continue
                raw_ts = event.get("timestamp")
                try:
                    sent_at = datetime.fromtimestamp(int(raw_ts) / 1000, tz=dt_timezone.utc)
                except (TypeError, ValueError, OSError):
                    sent_at = timezone.now()
                contact_name = messenger.fetch_user_name(sender_id) or sender_id
                try:
                    Message.objects.create(
                        channel="messenger",
                        contact=contact_name,
                        contact_id=sender_id,
                        direction="in",
                        subject="",
                        text=text,
                        message_id=message_id,
                        user_email=page_id,
                        created_at=sent_at,
                        is_read=False,
                    )
                except IntegrityError:
                    continue

        return JsonResponse({"status": "ok"})

    return HttpResponse(status=405)
