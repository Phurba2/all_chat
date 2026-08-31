from django.db import models

class Message(models.Model):
    CHANNELS = [
        ("whatsapp", "WhatsApp"),
        ("messenger", "Messenger"),
        ("email", "Gmail"),
    ]
    user_email = models.CharField(default="", blank=True)  # Track which user this message belongs to
    channel = models.CharField(choices=CHANNELS)
    contact = models.CharField()
    direction = models.CharField(max_length=3, choices=[("in", "Incoming"), ("out", "Outgoing")], default="in")
    text = models.TextField()
    subject = models.CharField(blank=True, default="")
    message_id = models.CharField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    summary = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.channel}] {self.contact}: {self.text[:50]}"