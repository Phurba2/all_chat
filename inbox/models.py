from django.db import models


class Message(models.Model):
    CHANNELS = [
        ("whatsapp", "WhatsApp"),
        ("instagram", "Instagram"),
        ("messenger", "Facebook Messenger"),
        ("email", "Email"),
        ("webchat", "Website Chat"),
    ]
    channel = models.CharField(max_length=20, choices=CHANNELS)
    contact = models.CharField(max_length=100)  # name or address of the other party
    direction = models.CharField(max_length=3, choices=[("in", "Incoming"), ("out", "Outgoing")], default="in")
    text = models.TextField()
    subject = models.CharField(max_length=255, blank=True, default="")
    message_id = models.CharField(max_length=255, blank=True, default="")  # email Message-ID / UID for dedup
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.channel}] {self.contact}: {self.text[:50]}"
