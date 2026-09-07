from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", RedirectView.as_view(url="channel/messenger/", permanent=False), name="inbox"),
    path("setup/", views.setup, name="setup"),
    path("setup/messenger/", views.setup_messenger, name="setup_messenger"),
    path("logout/", views.logout, name="logout"),
    path("channel/<str:channel>/", views.inbox, name="channel"),
    path("channel/<str:channel>/<str:contact>/", views.conversation, name="conversation"),

    path("messenger/webhook/", views.messenger_webhook, name="messenger_webhook"),
]
