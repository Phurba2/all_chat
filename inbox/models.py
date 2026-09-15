from django.db import models
from django.db.models import Q
from django.utils import timezone


class Message(models.Model):
    CHANNELS = [
        ("whatsapp", "WhatsApp"),
        ("messenger", "Messenger"),
        ("email", "Gmail"),
    ]
    DIRECTIONS = [("in", "Incoming"), ("out", "Outgoing")]

    user_email = models.CharField(max_length=320, default="", blank=True)
    channel = models.CharField(max_length=20, choices=CHANNELS)
    contact = models.CharField(max_length=320)
    contact_id = models.CharField(max_length=320, blank=True, default="")
    direction = models.CharField(max_length=3, choices=DIRECTIONS, default="in")
    text = models.TextField()
    subject = models.CharField(max_length=998, blank=True, default="")
    message_id = models.CharField(max_length=512, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    is_read = models.BooleanField(default=False)
    summary = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "user_email", "message_id"],
                condition=~Q(message_id=""),
                name="unique_external_message",
            ),
        ]

    def __str__(self):
        return f"[{self.channel}] {self.contact}: {self.text[:50]}"
